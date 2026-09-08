"""
orchestrator.py  -  the sequential pipeline + local LLM.

Pipeline order (deliberate, gated):
    Inventory agent  ->  low-stock flags  ->  Risk agent  ->  scored routes
    ->  Ollama (local) writes the plain-language answer.

If Ollama isn't running, it falls back to a templated summary so the demo
never breaks.

IMPORTANT (perf/UX): fetching inventory + scoring routes touches the shared
DB and live external APIs (NWS, OSRM, EIA) and is the slow part. Answering a
question with the LLM is cheap. So these are split into two calls:

    data = get_pipeline_data()               # run ONCE per session / refresh
    out  = answer_question(question, data)   # run per chat message (fast)

This lets the UI cache `data` across a whole conversation and only re-run the
cheap LLM call for each follow-up question - which is what makes the chat
feel conversational instead of re-computing two tables on every message.
"""
import os
import re
import json
import requests
import inventory_agent
import risk_agent

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
OLLAMA_TAGS_URL = f"{OLLAMA_HOST}/api/tags"
# Override with:  set OLLAMA_MODEL=qwen2.5:3b-instruct   (faster, but must be
# pulled first: `ollama pull qwen2.5:3b-instruct` - /api/generate returns a
# 404 "model not found", not a connection error, if it isn't).
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

# A hard 5s cap on chat answers turned out to mean "almost never actually
# gets an answer from Ollama" - even the 3B model doesn't reliably finish
# inside 5s on CPU, so nearly every question was quietly falling back to the
# templated summary instead. Reverted to a generous timeout so real answers
# actually come back; warm_up_ollama() below still absorbs the one-time
# model-load cost at pipeline-refresh time rather than on the first question.
GENERATE_TIMEOUT = 180   # first call after Ollama starts loads the model into
                         # RAM, which can genuinely take 60-120s for a 7B model
                         # on CPU (less for 3B, but still not instant). A short
                         # timeout looks like "unreachable" when it's really
                         # just still loading/generating.
HEALTHCHECK_TIMEOUT = 3  # /api/tags is instant if the server is up

_warmed_up = False  # module-level: warm up once per process, not per session


def ollama_status():
    """Cheap, fast check: is the Ollama SERVER reachable at all (not whether
    a generate call will succeed/be fast). Used by the UI for the sidebar
    badge, separately from whether the last chat answer used it."""
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=HEALTHCHECK_TIMEOUT)
        models = [m["name"] for m in r.json().get("models", [])]
        return {"server_up": True, "models": models, "model_pulled": MODEL in models}
    except Exception as e:
        return {"server_up": False, "models": [], "model_pulled": False, "error": str(e)}


def warm_up_ollama():
    """Force the model into RAM with a trivial generate call BEFORE the
    first real question is asked, so that cold-load cost lands on the
    pipeline-refresh spinner instead of on whoever asks first. Call once
    when the pipeline data is (re)loaded, not per-question."""
    global _warmed_up
    if _warmed_up:
        return
    try:
        requests.post(OLLAMA_GENERATE_URL, timeout=GENERATE_TIMEOUT, json={
            "model": MODEL, "prompt": "Reply with just: ready",
            "stream": False, "keep_alive": "30m",
            "options": {"num_predict": 5},
        })
        _warmed_up = True
    except Exception as e:
        print(f"[orchestrator] warm-up call failed (will retry on next refresh): {type(e).__name__}: {e}")


def _ask_ollama(prompt):
    try:
        r = requests.post(OLLAMA_GENERATE_URL, timeout=GENERATE_TIMEOUT, json={
            "model": MODEL, "prompt": prompt, "stream": False,
            "keep_alive": "30m",   # keep the model warm between chat turns so
                                    # only the FIRST message in a session is slow
            # Lower temperature keeps qwen2.5 on-task and in English. This model
            # is bilingual (Chinese/English) and drifts into Chinese mid-answer
            # at higher temperatures, especially in low-confidence spots; 0.2
            # plus the "respond in English" instruction in the prompt stops it.
            #
            # num_predict / repeat_penalty guard against a different failure
            # mode: given a repetitive structure in the data (e.g. many
            # shipments for the same store+item with the same risk score),
            # the model can lock into that pattern and keep extending it -
            # inventing extra "OPEN SHIPMENT ... ETA ..." rows well past
            # what's actually in the prompt - instead of stopping at a plain
            # summary. num_predict hard-caps generation length so a loop like
            # that can't produce a huge wall of text; repeat_penalty makes
            # the model less willing to repeat the same phrase/token pattern
            # in the first place.
            "options": {"temperature": 0.2, "num_predict": 260, "repeat_penalty": 1.3},
        })
        r.raise_for_status()
        return r.json()["response"].strip(), True
    except Exception as e:
        # Printed to the terminal running `streamlit run app.py` - this is
        # the fastest way to see WHY a given question fell back (timeout vs
        # connection refused vs model error) instead of guessing.
        print(f"[orchestrator] Ollama call failed: {type(e).__name__}: {e}")
        return None, False


RISK_METHODOLOGY_WORDS = ["factor", "method", "calculat", "formula", "weight", "how is risk",
                           "how do you", "what tools", "what data", "based on what", "risk model"]


def _wants_methodology(question):
    """Is this a meta-question about HOW risk is scored, rather than a
    question about a specific shipment/store? Answered from a fixed table,
    never the LLM - the weights below are exact code constants, not
    something that should be reworded or approximated by a model."""
    ql = question.lower()
    return any(w in ql for w in RISK_METHODOLOGY_WORDS)


# Mirrors the exact weights in risk_agent.score_route(). If those weights
# change, update this table too - it's intentionally a separate literal
# copy (not imported/computed from score_route) so this file doesn't need
# to import risk_agent's internals just to describe them.
RISK_METHODOLOGY_TABLE = [
    {"factor": "Weather alerts (destination)", "weight": "25%", "source": "NWS point-based severe alerts"},
    {"factor": "Distance", "weight": "20%", "source": "Route distance in km, capped at 300km = max risk"},
    {"factor": "Carrier/vendor reliability", "weight": "15%", "source": "Historical on-time delivery rate"},
    {"factor": "Road hazards (statewide)", "weight": "10%", "source": "NWS hazards along travel corridors"},
    {"factor": "Precipitation forecast", "weight": "10%", "source": "Open-Meteo graded forecast (non-severe weather)"},
    {"factor": "Diesel price", "weight": "10%", "source": "EIA diesel price, scored on a $3.50-5.00 band"},
    {"factor": "Rural destination", "weight": "10%", "source": "US Census rural flag (population < 20,000)"},
]


def _extract_known_store(question, inv, risks):
    """If the question names a store number that actually appears in this
    session's data, return it so the fallback can filter to just that store."""
    known = {f["store_number"] for f in inv["flags"]} | {r["store_number"] for r in risks}
    for tok in re.findall(r"\b\d{3,6}\b", question):
        n = int(tok)
        if n in known:
            return n
    return None


RISK_WORDS = ["risk", "delivery", "shipment", "carrier", "weather", "eta", "route", "late", "delay"]
INVENTORY_WORDS = ["stock", "inventory", "cover", "reorder", "on hand", "on-hand", "low", "level"]


def _question_focus(question):
    """Does this question care about delivery/shipment risk, stock/inventory
    levels, or (by default, if neither set of keywords hits) both? Shared by
    the offline fallback and the live LLM prompt so a plain 'stock level'
    question doesn't get a shipment-risk data dump it never asked for."""
    ql = question.lower()
    wants_risk = any(w in ql for w in RISK_WORDS)
    wants_inventory = any(w in ql for w in INVENTORY_WORDS)
    if not wants_risk and not wants_inventory:
        wants_risk = wants_inventory = True   # generic question -> show both
    return wants_risk, wants_inventory


def _fallback_text(question, inv, risks):
    """Templated answer used when Ollama is unreachable/slow. Unlike a fixed
    summary, this actually reads the question: it filters to a named store
    number if one appears, and emphasizes inventory vs. delivery-risk data
    based on keywords - so two different questions don't produce the same
    canned paragraph."""
    ql = question.lower()
    store = _extract_known_store(question, inv, risks)

    flags = inv["flags"]
    r_list = risks
    if store is not None:
        flags = [f for f in flags if f["store_number"] == store]
        r_list = [r for r in r_list if r["store_number"] == store]

    wants_risk, wants_inventory = _question_focus(question)

    lines = [f'(Ollama unreachable - answering "{question.strip()}" from live data '
             f'without the LLM{f", filtered to store {store}" if store else ""}.)']

    if wants_inventory:
        if store is not None:
            val = round(sum(f["inventory_value"] or 0 for f in flags), 2)
            lines.append(f"Store {store} has {len(flags)} item(s) under 14 days cover (${val:,.2f} at risk).")
        else:
            lines.append(f"{inv['at_risk']} of {inv['tracked']} tracked store-items are below "
                         f"the 14-day cover threshold (${inv['value_at_risk']:,.2f} of inventory "
                         f"value at risk).")
        for f in flags[:5]:
            lines.append(f"  - store {f['store_number']} {f['item_description']}: "
                         f"{f['days_of_cover']} days cover, ${f['inventory_value']:,.2f} at risk.")

    if wants_risk:
        if r_list:
            hi = [r for r in r_list if r["risk_band"] == "HIGH"]
            lines.append(f"{len(r_list)} inbound shipment(s) re-checked; {len(hi)} HIGH delivery risk.")
            for r in sorted(r_list, key=lambda x: -x["risk_score"])[:5]:
                lines.append(f"  - store {r['store_number']} {r['item_description']}: "
                             f"risk {r['risk_score']}/100 ({r['risk_band']}), "
                             f"ETA {r['updated_eta']}, {r['distance_km']} km, "
                             f"${r['shipment_value']:,.2f} in transit.")
        else:
            lines.append("No inbound shipments needed re-checking" +
                         (f" for store {store}." if store else "."))

    return "\n".join(lines)


def _match_items(question, catalog):
    """Loose keyword match of the question against item descriptions, e.g.
    'price of captain morgan spiced rum' -> the matching catalog row(s).
    Deliberately simple (word overlap) since the catalog is only ~25 items."""
    q_words = set(re.findall(r"[a-z']+", question.lower()))
    STOP = {"the", "a", "an", "of", "is", "what", "whats", "price", "cost",
            "current", "for", "how", "much", "does", "do", "we", "have",
            "in", "stock", "and", "or", "to", "on"}
    q_words -= STOP
    if not q_words:
        return []
    scored = []
    for item in catalog:
        item_words = set(re.findall(r"[a-z']+", item["item_description"].lower()))
        overlap = q_words & item_words
        if overlap:
            scored.append((len(overlap), item))
    scored.sort(key=lambda t: -t[0])
    return [item for _, item in scored[:3]]


# --------------------------------------------------------------------------
# Guardrails - deterministic pre-checks run BEFORE the pipeline/LLM.
# Adapted from a teammate's pattern (Vishnu's inventory agent coordinator):
# catch out-of-scope questions with cheap keyword checks and decline
# honestly, rather than letting an LLM improvise an answer to a question
# your data literally can't support. Kept short and scoped to this
# project's data (inventory + delivery risk only) rather than copying his
# full category-matching logic, which was built for his own dataset shape.
# --------------------------------------------------------------------------
SUBJECTIVE_WORDS = ["best", "worst", "favorite", "favourite", "most popular",
                    "top selling", "highest rated", "recommend"]
HYPOTHETICAL_INDICATORS = ["what if", "would it", "suppose we", "if we sold",
                           "if we ordered", "hypothetically"]
IDENTITY_INDICATORS = ["are you an ai", "are you a real person", "who are you",
                       "are you human", "what company do you"]
FALSE_MEMORY_INDICATORS = ["you said", "yesterday you", "earlier you told",
                           "last time you"]


def _out_of_scope_reply(question):
    """Returns an honest decline string if the question is one this project's
    data literally can't answer, or None if it's fine to proceed. Runs before
    the LLM call so these questions never reach Ollama at all - a subjective
    or hypothetical question doesn't get a fabricated-sounding answer, it
    gets an accurate 'I can't answer that' instead."""
    ql = question.lower()
    if any(w in ql for w in SUBJECTIVE_WORDS):
        return ("I can only report on stock levels, reorder status, and delivery risk - "
                "I don't have data on brand popularity, ratings, or sales rankings.")
    if any(w in ql for w in HYPOTHETICAL_INDICATORS):
        return ("I can only report on current, real inventory and shipment data - "
                "I don't simulate hypothetical scenarios like future sales or orders.")
    if any(w in ql for w in IDENTITY_INDICATORS):
        return ("I'm an AI assistant reporting on this project's live inventory and "
                "delivery-risk data - no company or personal identity, just the data.")
    if any(w in ql for w in FALSE_MEMORY_INDICATORS):
        return ("I don't have memory of past conversations beyond this session - "
                "please ask your question directly and I'll check current data.")
    return None


def _price_lookup_answer(question, catalog):
    """A deterministic, DB-only price answer. Only used as a FALLBACK when
    Ollama is unreachable (see answer_question) - never pre-empts the LLM,
    and only fires for genuinely simple lookups so a multi-part question
    doesn't get reduced to just the price list."""
    ql = question.lower()
    if not any(w in ql for w in ["price", "cost", "how much", "$"]):
        return None
    # Bail out on anything that signals more than a plain lookup - a complex
    # question deserves the LLM (or the broader fallback), not a price list.
    complex_markers = ["why", "also", "explain", "compare", "difference",
                        "every", "all ", "and fetch", "and show", "reason"]
    if any(m in ql for m in complex_markers):
        return None
    matches = _match_items(question, catalog)
    if len(matches) == 1:
        it = matches[0]
        return (f"{it['item_description']} is ${it['state_bottle_retail']:.2f} retail "
                f"(wholesale cost ${it['state_bottle_cost']:.2f}) per bottle. "
                f"{it['total_on_hand']} bottles on hand across tracked stores.")
    if len(matches) > 1:
        lines = [f"Found {len(matches)} matching items:"]
        for it in matches:
            lines.append(f"  - {it['item_description']}: ${it['state_bottle_retail']:.2f} retail, "
                         f"{it['total_on_hand']} on hand")
        return "\n".join(lines)
    return None


def get_pipeline_data():
    """Run the Inventory -> Risk pipeline once. Slow part (DB + live APIs).

    Risk source is chosen automatically:
      * DATABRICKS path - if databricks_sync.py has landed a `gold_risk_scores`
        table locally, read those pre-computed scores directly (all open
        shipments, already scored by the Gold notebook). This is the current
        architecture and needs no live API calls at query time.
      * LOCAL-SIM path - otherwise score the flagged reorders live via
        risk_agent.run() against gold_shipments_open (build_database + simulate).
    """
    inv = inventory_agent.summary()

    warm_up_ollama()  # absorb the one-time model-load cost here, not on the
                       # first chat question

    risks = risk_agent.read_gold_scores()      # Databricks Gold (pre-computed)
    risk_source = "databricks_gold"
    if risks is None:                           # no synced Gold table
        risks = risk_agent.run(inv["flags"])    # local live scoring, gated on flags
        risk_source = "live_scoring"
    print(f"[orchestrator] risk source: {risk_source} ({len(risks)} scored shipments)")

    catalog = inventory_agent.item_catalog()
    any_live = any(r.get("data_live") for r in risks)
    return {"inventory": inv, "risks": risks, "catalog": catalog,
            "external_data_live": any_live, "risk_source": risk_source}


def answer_question(question, data, history=None):
    """Answer one question against already-fetched pipeline data.

    Ollama gets first shot at EVERY question (it has the full catalog,
    inventory, and risk data in its prompt, so it can handle multi-part or
    nuanced questions). The deterministic shortcuts below only kick in if
    Ollama is unreachable/times out - they are a safety net, not a
    replacement for the LLM.

    history: optional list of {"role": "user"/"assistant", "content": str} from
    earlier turns in the SAME session, so follow-up questions ("what about
    store 3952?") have conversational context.
    """
    inv, risks, catalog = data["inventory"], data["risks"], data.get("catalog", [])

    # Guardrail: decline out-of-scope questions before touching the LLM at
    # all - see _out_of_scope_reply above.
    decline = _out_of_scope_reply(question)
    if decline:
        return {"answer": decline, "inventory": inv, "risks": risks,
                "llm_live": False, "external_data_live": data["external_data_live"],
                "direct_lookup": False}

    convo = ""
    if history:
        recent = history[-6:]
        convo = "\n".join(f"{h['role']}: {h['content']}" for h in recent) + "\n\n"

    # Narrow the data to what the question is actually about, rather than
    # handing the model the entire 34-item catalog + first-8 flags/risks and
    # hoping it finds the right rows itself. Without this, a question about
    # one named item ("Black Velvet") got the same undifferentiated data
    # blob as a generic question, and the model - especially under a tight
    # token budget - tended to answer from whatever was first in the blob
    # (e.g. the highest-value item) instead of the item actually asked
    # about. _match_items already existed for this but was previously only
    # wired up to the offline fallback path, not the live LLM prompt.
    wants_risk, wants_inventory = _question_focus(question)
    matches = _match_items(question, catalog)
    if matches:
        # _match_items caps itself at 3 ranked rows for disambiguation
        # between *different* products, but one product can legitimately
        # span several catalog rows (e.g. Black Velvet in 5 bottle sizes,
        # each its own row with the identical item_description). Expand
        # back out to every row sharing the matched description(s) so a
        # multi-size item doesn't get silently truncated to 3 sizes.
        matched_descs = {m["item_description"] for m in matches}
        catalog_for_prompt = [c for c in catalog if c["item_description"] in matched_descs]

        # Filter flags/risks to just this item. Note: an empty result here
        # is a REAL, meaningful answer ("this item has no low-stock flags /
        # no at-risk shipments right now") - it must NOT silently fall back
        # to the unrelated global top-8, or the model ends up talking about
        # some other item's flags/shipments instead of correctly reporting
        # "none for this item".
        item_flags = [f for f in inv["flags"] if f["item_description"] in matched_descs]
        item_risks = [r for r in risks if r["item_description"] in matched_descs]
    else:
        catalog_for_prompt = catalog
        item_flags = inv["flags"][:8]
        item_risks = risks[:8]

    # Only include each section if the question is actually about it (or
    # it's a generic question, where both are wanted). This is what stops a
    # plain "stock level" question from getting swamped by a large
    # shipment-risk dump: previously, an item with e.g. 17 real matching
    # shipments would push that data into the prompt in full, and it easily
    # outweighed the (comparatively small) stock/catalog data, so the model
    # summarized the shipments and ignored stock levels entirely - which is
    # what happened for "Summarize the stock level information of Black
    # Velvet".
    flags_for_prompt = item_flags if wants_inventory else []
    risks_for_prompt = (item_risks if wants_risk else [])
    # Still cap risk rows even when they ARE wanted and there's a real
    # match, so one heavily-shipped item can't dominate the prompt either.
    risks_for_prompt = sorted(risks_for_prompt, key=lambda r: -r.get("risk_score", 0))[:8]

    focus_note = ""
    if matches:
        focus_note += (
            f"The user's question is specifically about: {', '.join(sorted(matched_descs))}. "
            "The catalog/flag/risk data below has already been filtered to just this item - "
            "answer using only these rows, not any other product.\n\n"
        )
    if not wants_risk:
        focus_note += ("The question is about stock/inventory levels, not delivery or shipment "
                        "risk - do not discuss shipments, carriers, ETAs, or risk scores unless "
                        "the user explicitly asks about them.\n\n")
    elif not wants_inventory:
        focus_note += ("The question is about delivery/shipment risk, not stock levels - do not "
                        "discuss bottle counts, reorder points, or days of cover unless the user "
                        "explicitly asks about them.\n\n")

    catalog_lines = [
        f"{c['item_description']} ({c['category_name']}): ${c['state_bottle_retail']:.2f} retail, "
        f"${c['state_bottle_cost']:.2f} wholesale, {c['total_on_hand']} bottles on hand total, "
        f"supplier/vendor: {c.get('vendor_name') or 'unknown'}"
        for c in catalog_for_prompt
    ]

    prompt = (
        "You are a supply-chain assistant for an Iowa liquor distributor. "
        "ALWAYS respond in English, regardless of the language of the question. "
        "Answer the user's question in 4-6 sentences using ONLY the data below - "
        "never invent a supplier, store, price, or number that is not present here; "
        "if a fact isn't in the data, say so plainly instead of guessing. "
        "If the question refers to the earlier conversation, use that context.\n\n"
        "The risk_score, days_of_cover, inventory_value, and reorder flags below are "
        "ALREADY COMPUTED by the Inventory and Risk agents. Do not recalculate, "
        "question, second-guess, or contradict these numbers - your job is only to "
        "explain what they mean in plain language, not to decide or verify them.\n\n"
        "Do NOT enumerate every row of data one by one, even if several rows look "
        "similar (e.g. many shipments for the same store with the same risk score). "
        "Summarize instead: state the count, the shared risk level, and the "
        "earliest/latest or most important date or two - never list each one "
        "individually, and never continue a list beyond what's in the data below.\n\n"
        f"{focus_note}"
        f"{convo}"
        f"User question: {question}\n\n"
        f"Item catalog (name (category): retail price, wholesale cost, bottles on hand, supplier): "
        f"{'; '.join(catalog_lines)}\n\n"
        f"Inventory: {inv['at_risk']} of {inv['tracked']} store-items under 14 days cover. "
        f"Total inventory value tracked: ${inv['total_inventory_value']:,.2f}. "
        f"Value at risk (low-stock items): ${inv['value_at_risk']:,.2f}.\n"
        f"Flagged (store, item, days, value): "
        f"{[(f['store_number'], f['item_description'], f['days_of_cover'], f['inventory_value']) for f in flags_for_prompt]}\n"
        f"Route risk scores: {json.dumps(risks_for_prompt)}\n\n"
        "Reminder: reply in English only, and only with facts from the data above."
    )
    text, live_llm = _ask_ollama(prompt)
    direct_lookup = False
    if text is None:
        # Ollama unreachable/timed out - try a deterministic price answer
        # first (for simple lookups), then the broader question-aware
        # template as a last resort.
        text = _price_lookup_answer(question, catalog)
        if text is not None:
            direct_lookup = True
        else:
            text = _fallback_text(question, inv, risks)

    return {
        "answer": text,
        "inventory": inv,
        "risks": risks,
        "llm_live": live_llm,
        "external_data_live": data["external_data_live"],
        "direct_lookup": direct_lookup,
        # The EXACT rows the model (or fallback) answered from, so the UI can
        # render a real table instead of relying on the LLM to format one in
        # prose - text and table are guaranteed to describe the same data
        # since both come from flags_for_prompt/risks_for_prompt.
        "flags_table": flags_for_prompt,
        "risks_table": risks_for_prompt,
        "methodology_table": RISK_METHODOLOGY_TABLE if _wants_methodology(question) else None,
    }


def answer(question="Which stores are at risk of stockout and are their reorders in danger?"):
    """Convenience one-shot entry point (used by CLI / non-chat callers)."""
    data = get_pipeline_data()
    return answer_question(question, data)


if __name__ == "__main__":
    print("Ollama status:", ollama_status())
    out = answer()
    print(out["answer"])
    print("\n[LLM live:", out["llm_live"], "| external data live:", out["external_data_live"], "]")