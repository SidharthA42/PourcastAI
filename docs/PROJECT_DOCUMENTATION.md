# PourCastAI — Project Documentation

A multi-agent AI decision-support system for Iowa liquor supply chain management. This document explains the whole project end to end — what it does, why it's built this way, and how every piece fits together — so anyone on the team (or grading it) can understand it without reading the code first.

---

## 1. What this project is

Iowa is a **control state** for liquor: the state runs one central distribution point that supplies every licensed retailer statewide, rather than retailers buying directly from vendors. PourCastAI models that real supply chain — a warehouse in Ankeny shipping to ~1,700 real Iowa stores — and answers two questions a real distributor asks every day:

1. **Which stores are about to run out of stock, and how urgently should we reorder?** (Inventory Agent)
2. **Of the shipments already moving to fix that, which ones are at risk of being late, and why?** (Risk & Logistics Agent)

A local LLM (via Ollama) sits on top of both agents and answers plain-language questions about the results — "which stores are at risk," "what's the highest-risk shipment," "what's the price of X" — through a chat interface, backed by a dashboard and a browsable product catalog.

This is a university case-study project. It is built to be **honest about what's real and what's simulated** — that distinction is labeled everywhere, from the database schema comments to the UI badges, because a project that pretends simulated data is real (or vice versa) would undermine its own methodology section.

---

## 2. Data sources

### 2.1 The foundation: Iowa Liquor Sales (Kaggle) — REAL, static

A ~3.4 GB CSV of real historical liquor sales transactions from the state of Iowa, covering roughly 2012–2020. `build_database.py` reads this once (via DuckDB, which can query the file without loading it all into memory) and slices it down to a working set: the **25 best-selling items** across the **20 highest-volume stores**, over the most recent 12 months of data available.

Every dimension table (`dim_item`, `dim_store`, `dim_vendor`) and the sales fact table (`fact_sales`) trace directly back to this file. Nothing in those four tables is invented.

### 2.2 Five live external APIs — REAL, called at runtime

These are what make the "dynamic data sources" part of the project genuinely dynamic, rather than just re-reading a CSV on a schedule. All are free.

| Source | What it provides | Key needed? | Feeds into |
|---|---|---|---|
| **NWS** (National Weather Service) — weather alerts | Point-based severe weather alerts (warnings/watches) at a specific destination | No | Risk score — 25% |
| **NWS** — road hazards | Statewide active advisories relevant to travel (winter storms, ice, fog, high wind, flooding) — same API domain as above, different query | No | Risk score — 10% |
| **OSRM** (Open Source Routing Machine) | Real driving distance and duration, Ankeny → each store | No | Risk score — 20% |
| **Open-Meteo** | Graded precipitation probability and wind speed forecast (the *standard land forecast API*, not the marine one — this project models trucks, not ships) | No | Risk score — 10% |
| **EIA** (Energy Information Administration) | Current diesel price ($/gal, Midwest) | Yes (free) | Risk score — 10% |

**Why both an NWS alert check and a separate NWS road-hazard check, and both an NWS alert check and an Open-Meteo forecast?** They cover different gaps:
- NWS alerts only fire for genuinely *severe* weather. A truck can be meaningfully slowed by heavy-but-non-severe snow that never triggers an alert — Open-Meteo's graded forecast catches that.
- The alert check is **point-based** (just the destination store). The road-hazard check is **statewide** — a closed stretch of I-80 matters even if the destination store itself has no active alert.

### 2.3 One build-time-only source: US Census — REAL, fetched once

County-level population, used to classify a store's county as rural or urban (`dim_store.rural_flag`), which feeds 10% of the risk score — the idea being that rural counties genuinely have thinner road infrastructure, independent of weather or distance on any given day.

This is **not** called live per-question like the five sources above — population doesn't change day to day, so it's fetched **once, at database build time**, in `build_database.py`, and stored directly on `dim_store`. As of May 2026, the Census Bureau requires an API key for every request (a policy change); without one, a small hardcoded fallback table covers Iowa's biggest counties, and the actual outcome (live vs. fallback) is recorded in the database itself (`ext_cache.census_live`) so it's always possible to check what really happened at the last build, rather than guessing.

**Documented simplification:** "rural" here means county population < 20,000 — a threshold-based proxy, not the official USDA Rural-Urban Continuum Code. Good enough for this project's purposes, but worth stating plainly rather than implying more precision than it has.

### 2.4 Simulated data — clearly derived, not invented

The Kaggle dataset has sales, but no inventory levels and no shipments — those don't exist as public data. `simulate.py` generates both, but **derived from the real sales**, not out of thin air:

- Every store+item starts with an opening stock (21 days of average demand).
- Each day, stock is depleted by the **real** `bottles_sold` for that day.
- When stock crosses a calculated reorder point, a replenishment shipment is generated — realistic quantity, real Ankeny origin coordinates, real destination store coordinates.

So every simulated shipment exists *because* a real historical sale drained the shelf. That link is the core of the methodology section's honesty story.

### 2.5 Summary table — REAL vs SIMULATED, by table

| Table | Status | Source |
|---|---|---|
| `dim_vendor` (name only) | REAL | Kaggle |
| `dim_vendor` (lead_time_days, lead_time_std, reliability_score) | SIMULATED | Generated, documented assumptions |
| `dim_item` | REAL | Kaggle |
| `dim_store` (everything except population/rural_flag) | REAL | Kaggle |
| `dim_store.population`, `rural_flag` | REAL (or documented fallback) | US Census, fetched once at build time |
| `dim_carrier` | Ruan is REAL; two others SIMULATED (for comparison) | — |
| `fact_sales` | REAL | Kaggle |
| `fact_inventory_snapshot` | SIMULATED | Derived from real sales via depletion simulation |
| `fact_shipments` | SIMULATED | Generated when simulated stock crosses the reorder point |
| `ext_cache` | Neither — operational cache | Written by n8n / build scripts |

---

## 3. Database schema — why 4 dimension tables and 3 fact tables

One shared SQLite file (`pourcast.db`), opened by every agent, so the Inventory agent and Risk agent can never disagree about what a given item or store actually is.

### 3.1 The two shared keys

Every table that references a product or a store uses the exact same key:
- `item_number` — a product/SKU
- `store_number` — a retail store

This is what makes it a genuinely **shared** database rather than five agents each with their own private copy of "what item 26827 is."

### 3.2 Dimensions (the things)

| Table | What it represents |
|---|---|
| `dim_vendor` | Who supplies a product (the brand owner, since Iowa is a control state) |
| `dim_item` | One row per product/SKU — name, category, price, bottle size, case pack |
| `dim_store` | One row per retail store — name, address, real lat/long (needed for routing), county, population, rural flag |
| `dim_carrier` | Who drives the truck |

### 3.3 Facts (the events)

| Table | Grain (one row per...) | What it captures |
|---|---|---|
| `fact_sales` | store × item × sale date | A real historical transaction |
| `fact_inventory_snapshot` | store × item × simulated day | Stock level, reorder point, safety stock, days of cover on that day |
| `fact_shipments` | one replenishment order | A truck run from Ankeny to a store, with quantity, carrier, promised ETA |

Each is a fundamentally different kind of event with different columns — they can't be merged into one table without losing information or duplicating it everywhere.

### 3.4 Gold views — one per agent

Rather than every agent writing its own joins, two views sit on top of the raw tables:

- **`gold_inventory_health`** — the Inventory agent's entire world. Joins `fact_inventory_snapshot` with `dim_item`/`dim_store`, computes `inventory_value` and `low_stock_flag`, and filters to only the most recent snapshot date.
- **`gold_shipments_open`** — the Risk agent's entire world. Joins `fact_shipments` with `dim_item`/`dim_store`/`dim_vendor`/`dim_carrier`, computing `shipment_value` and carrying through rurality.

### 3.5 `ext_cache` — not part of the star schema

A small key/value table used for operational caching, separate from the REAL/SIMULATED data model:
- `diesel_price` / `diesel_price_live` — written by n8n's scheduled refresh job, read by the Risk agent before ever hitting the live EIA API itself, so a chat question never blocks on that call.
- `census_live` — written once at DB build time, records whether the last Census fetch actually succeeded (used by `check_data_sources.py` to report honestly).

---

## 4. The reorder-point model (Inventory Agent's core logic)

Early versions of this project used a flat rule: "reorder when stock drops below 14 days of average demand." That's arbitrary — it treats a steady seller and a wildly unpredictable one identically, giving both the same buffer.

The current model is a proper **statistical (s, S) safety-stock formula**, adapted from a teammate's (Vishnu's) inventory agent and reimplemented against this project's schema:

```
safety_stock = Z × √(lead_time_avg × demand_std² + demand_avg² × lead_time_std²)
reorder_point = demand_avg × lead_time_avg + safety_stock
```

- `Z = 1.645` — the standard normal quantile for a 95% one-tailed service level (the same value `scipy.stats.norm.ppf(0.95)` would give).
- `demand_avg`, `demand_std` — computed from the item's **real** daily sales, including zero-sale days (so the variability reflects reality, not just the days that happened to have a sale).
- `lead_time_avg` — from `dim_vendor.lead_time_days` (simulated, 2–10 days).
- `lead_time_std` — assumed as 20% of `lead_time_days` per vendor (a documented estimate — no public dataset tracks Iowa's actual vendor delivery-time variability).

**In plain terms:** an item that sells unpredictably, or comes from a vendor with inconsistent delivery times, gets a bigger safety buffer than a steady seller from a reliable vendor — even if both average the same daily sales. `low_stock_flag` now means "on-hand at or below this calculated threshold," not an arbitrary day count.

`sensitivity_check.py` tests this formula against alternative assumptions (90%/95%/99% service level, 10%/20%/30% lead-time variability) so the choice of 95%/20% is backed by a citable comparison rather than an unexplained pick.

---

## 5. The Risk & Logistics score (Risk Agent's core logic)

Only shipments tied to a flagged low-stock item get scored — this is a deliberately **gated** pipeline (Risk agent runs after, and depends on, the Inventory agent's output), not an independent process.

Seven weighted factors combine into a 0–100 score:

| Factor | Weight | Source |
|---|---|---|
| Weather alerts (destination-specific, severe only) | 25% | NWS |
| Road hazards (statewide) | 10% | NWS |
| Precipitation/wind forecast (graded, non-severe included) | 10% | Open-Meteo |
| Distance | 20% | OSRM |
| Diesel price | 10% | EIA |
| Vendor reliability | 15% | Simulated |
| Rural delivery | 10% | US Census-derived |

**Risk bands:** HIGH (≥60), MEDIUM (≥30), LOW (below). A shipment's promised ETA is stretched further out if either the weather-alert or forecast signal is bad *and* a real drive-time estimate exists.

---

## 6. Multi-agent architecture

### 6.1 The pipeline

```
Inventory Agent  →  Risk & Logistics Agent  →  Local LLM (Ollama)
(low-stock flags)   (scores only flagged        (plain-language answer,
                      shipments)                  guardrailed)
```

This sequential, gated design (each stage only processes what the previous stage flagged) is deliberate — it's cheaper than scoring every shipment, and it mirrors how a real distributor would actually triage: figure out what's low first, *then* check whether the fix is at risk.

### 6.2 Team ownership

| Agent | Owner | File(s) |
|---|---|---|
| Inventory Agent | Vishnu (approach adapted into this project) / Sid (integration) | `inventory_agent.py`, reorder logic in `simulate.py` |
| Risk & Logistics Agent | Sid | `risk_agent.py`, `risk_tools.py` |
| Orchestration / LLM | Sid | `orchestrator.py` |
| UI | Sai Charan | `app.py` |
| Market Analysis | Gaurangi | (separate agent, not covered in this doc) |
| Demand Forecast | Tania | (separate agent, not covered in this doc) |

### 6.3 Two ways the pipeline is wired together

- **`orchestrator.py`** (what the live Streamlit app actually uses): splits the pipeline into `get_pipeline_data()` (slow — hits the DB and live APIs, cached once per chat session) and `answer_question()` (fast — just the LLM call). This is what makes follow-up chat questions answer instantly instead of re-querying everything each time.
- **`agent_graph.py`** (the explicit LangGraph version, for the "multi-agent framework" requirement and as the reference architecture for the report): the same agents wired as nodes in a real graph — `guardrail → (inventory → risk → answer)` or `guardrail → answer` directly if the question is out of scope. Runnable standalone: `python agent_graph.py`. It re-fetches inventory/risk on every call rather than caching, which is fine for a single reference run but would be slower if swapped in behind live chat.

### 6.4 Guardrails and the locked prompt

Two reliability measures, adapted from a teammate's pattern:

1. **Out-of-scope guardrail** — before anything touches the LLM, a fast keyword check catches subjective questions ("what's the best whiskey"), hypotheticals ("what if we ordered more"), identity questions, and false-memory references, and returns an honest decline immediately.
2. **Locked LLM prompt** — the prompt explicitly tells the LLM the risk scores and reorder flags are *already computed*; its job is to explain them in plain language, not recalculate, question, or contradict them. This prevents the LLM from "correcting" a number it happens to disagree with.

### 6.5 Fallback behavior — the system never breaks

If Ollama is unreachable or times out, `orchestrator.py` falls back to a **question-aware templated answer** (filters to a named store if one's mentioned, emphasizes inventory vs. risk data based on keywords) rather than a single generic canned response — so different questions still get different answers even without the LLM. A deterministic price-lookup shortcut also exists for simple "what's the price of X" questions, used only as a fallback when the LLM is down, never pre-empting it.

---

## 7. The UI (Streamlit)

Three tabs, plus a sidebar:

- **💬 Chat** — real conversational chat (`st.chat_message`/`st.chat_input`, pinned to the bottom of the page). Conversation history persists across the session and is passed to the LLM for follow-up context. Each answer is labeled with its source (Ollama live / templated fallback / direct database lookup).
- **📊 Dashboard** — inventory table and delivery-risk table (both with CSV export), plus charts: days-of-cover distribution, value-at-risk by county, risk-band counts.
- **🛒 Catalog** — a browsable product grid: category-icon placeholder, name, price, bottle size/case pack, total stock, with search and category filtering. Clicking an item opens a detail dialog showing full pricing, supplier info (including lead time and its variability), packaging, and a per-store breakdown of stock/reorder status.

**Sidebar** shows live-data badges (external APIs, Ollama status) and quick stats, with a manual "Refresh data" button — the pipeline only re-runs when explicitly refreshed, not on every chat message, which is what keeps the chat fast.

---

## 8. n8n automation — what it actually does

Two independent scheduled workflows, running as a background process separate from the Streamlit app:

### 8.1 Nightly DB Rebuild (2 AM)
`Nightly 2AM trigger → build_database.py → simulate.py → Done`

Re-slices the Kaggle CSV and regenerates inventory/shipments, so the shared database refreshes itself every night with no manual step. Since `simulate.py` anchors its simulated dates to "today" each time it runs, this also keeps shipment ETAs looking current rather than drifting into the past.

### 8.2 API Cache Refresh (every 30 minutes)
`Every-30-min trigger → n8n/cache_refresh.py`

Polls the live EIA diesel price and writes it into `ext_cache`. The Risk agent checks this cache *first*, before ever calling the EIA API directly — so a chat question's latency never depends on an external API responding in real time; n8n has already done that polling in the background.

### 8.3 Running it

Two options, documented in full in `README.md`:
- **`npx n8n`** (recommended) — runs n8n as a normal local process, no Docker. Requires setting `NODES_EXCLUDE=[]` first, since n8n 2.0 disables the Execute Command node by default (a security default for shared instances, safe to re-enable on a single-user local one).
- **Docker** (`docker compose build && docker compose up -d`) — builds a custom image (`n8n/Dockerfile`) with Python baked in at build time, using the `-debian` image tag specifically, since n8n's default tag ships without any package manager as of the v2.x image line.

Workflows are imported from `n8n/workflows/*.json` and must be explicitly activated (Publish/Active toggle) for the schedule to actually run.

---

## 9. Tools and frameworks

| Layer | Tool | Why |
|---|---|---|
| Data loading | DuckDB | Queries the 3.4 GB CSV without loading it all into memory |
| Shared database | SQLite | Single-file, zero-setup, easy for every teammate to run locally and get an identical DB (`random.seed(42)` ensures this) |
| UI | Streamlit | Fast to build a chat + dashboard interface in pure Python |
| Charts | Altair | Ships with Streamlit, no extra install |
| LLM | Ollama (`qwen2.5:7b-instruct`, local) | Keeps the whole system local — no data or API keys ever leave the machine, directly satisfying the project's security/privacy property |
| Agent orchestration | LangGraph | Explicit multi-agent graph wiring, satisfies "framework beyond raw Python" |
| Automation | n8n | Scheduled jobs (nightly rebuild, API cache refresh) outside the Python app itself |
| Env management | python-dotenv | Loads API keys from `.env`, never hardcoded |

---

## 10. Setup — step by step

### 10.1 Prerequisites
- Python 3.10+ with a virtual environment
- [Ollama](https://ollama.com) installed, with a model pulled: `ollama pull qwen2.5:7b-instruct`
- Node.js (for running n8n via `npx`) — optional, only needed for automation
- The Kaggle "Iowa Liquor Sales" CSV, placed at `data/raw/Iowa_Liquor_Sales.csv`

### 10.2 Install dependencies
```powershell
pip install -r requirements.txt
```

### 10.3 API keys (optional but recommended)
Copy `.env.example` to `.env` and fill in:
- `EIA_API_KEY` — free, from eia.gov/opendata/register.php (needed for live diesel price)
- `CENSUS_API_KEY` — free, from api.census.gov/data/key_signup.html (required as of May 2026 for live county population; without it, a static fallback table is used and the project still runs correctly)

Without either key, the app still works end to end — it just uses documented fallback values instead of live ones.

### 10.4 Build the database
```powershell
python build_database.py
python simulate.py
```
Re-run both any time `schema.sql` changes, or any time you want fresh simulated dates anchored to today.

### 10.5 Verify data sources (optional but useful before a demo)
```powershell
python check_data_sources.py
```
Reports LIVE/FALLBACK for all 6 external sources at once.

### 10.6 Run the app
```powershell
streamlit run app.py
```

### 10.7 (Optional) Run the automation layer
```powershell
$env:NODES_EXCLUDE = "[]"
npx n8n
```
Then import both workflows from `n8n/workflows/` in the n8n UI and activate them.

---

## 11. Known assumptions and limitations (worth stating plainly in the report)

- **`lead_time_std` = 20% of `lead_time_days`** — an estimate, since no public dataset tracks Iowa's real vendor delivery-time variability. Tested against alternatives in `sensitivity_check.py`.
- **Rural classification = county population < 20,000** — a simplification, not the official USDA Rural-Urban Continuum Code.
- **Service level = 95% (Z = 1.645)** — a standard choice, also tested against 90%/99% in `sensitivity_check.py`.
- **Dataset scope** — 25 items × 20 stores (the busiest by volume), not the full statewide dataset, to keep the demo fast and the data manageable.
- **Simulated dates are shifted to anchor on "today"** at whatever point `simulate.py` last ran — the underlying day-by-day depletion math still uses the real historical sales pattern, only the displayed calendar dates are shifted forward.
- **Campus network may block some external APIs** — if a source shows FALLBACK unexpectedly, try again off-campus (e.g. mobile hotspot) before assuming it's broken.
