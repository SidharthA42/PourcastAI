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
# Override with:  set OLLAMA_MODEL=qwen2.5:3b-instruct   (much faster on CPU)
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

GENERATE_TIMEOUT = 180   # first call after Ollama starts loads the model into
                         # RAM, which can genuinely take 60-120s for a 7B model
                         # on CPU. A short timeout looks like "unreachable"
                         # when it's actually just still loading.
HEALTHCHECK_TIMEOUT = 3  # /api/tags is instant if the server is up


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
            "options": {"temperature": 0.2},
        })
        r.raise_for_status()
        return r.json()["response"].strip(), True
    except Exception as e:
        # Printed to the terminal running `streamlit run app.py` - this is
        # the fastest way to see WHY a given question fell back (timeout vs
        # connection refused vs model error) instead of guessing.
        print(f"[orchestrator] Ollama call failed: {type(e).__name__}: {e}")
        return None, False


def _extract_known_store(question, inv, risks):
    """If the question names a store number that actually appears in this
    session's data, return it so the fallback can filter to just that store."""
    known = {f["store_number"] for f in inv["flags"]} | {r["store_number"] for r in risks}
    for tok in re.findall(r"\b\d{3,6}\b", question):
        n = int(tok)
        if n in known:
            return n
    return None


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

    wants_risk = any(w in ql for w in
        ["risk", "delivery", "shipment", "carrier", "weather", "eta", "route", "late", "delay"])
    wants_inventory = any(w in ql for w in
        ["stock", "inventory", "cover", "reorder", "on hand", "on-hand", "low"])
    if not wants_risk and not wants_inventory:
        wants_risk = wants_inventory = True   # generic question -> show both

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

    catalog_lines = [
        f"{c['item_description']} ({c['category_name']}): ${c['state_bottle_retail']:.2f} retail, "
        f"${c['state_bottle_cost']:.2f} wholesale, {c['total_on_hand']} bottles on hand total, "
        f"supplier/vendor: {c.get('vendor_name') or 'unknown'}"
        for c in catalog
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
        f"{convo}"
        f"User question: {question}\n\n"
        f"Item catalog (name (category): retail price, wholesale cost, bottles on hand, supplier): "
        f"{'; '.join(catalog_lines)}\n\n"
        f"Inventory: {inv['at_risk']} of {inv['tracked']} store-items under 14 days cover. "
        f"Total inventory value tracked: ${inv['total_inventory_value']:,.2f}. "
        f"Value at risk (low-stock items): ${inv['value_at_risk']:,.2f}.\n"
        f"Flagged (store, item, days, value): "
        f"{[(f['store_number'], f['item_description'], f['days_of_cover'], f['inventory_value']) for f in inv['flags'][:8]]}\n"
        f"Route risk scores: {json.dumps(risks[:8])}\n\n"
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
