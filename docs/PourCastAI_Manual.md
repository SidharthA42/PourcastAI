# PourCastAI — Complete Project Manual

*A multi-agent AI decision-support system for Iowa liquor supply-chain management.*

This document is meant to be read start-to-finish by someone who has **never seen the
project before**. By the end you should understand what it does, why every piece
exists, how the data flows from a raw Kaggle CSV all the way to a chatbot answer,
and exactly how to set the whole thing up from an empty folder.

If you only remember one sentence: **PourCastAI reads real Iowa liquor sales,
simulates the inventory and shipments those sales imply, scores every inbound
shipment for delivery risk using live weather/routing/fuel data, and lets you ask
plain-English questions about it — with a local LLM writing the answers.**

---

## Table of contents

**Part I — Understand the project**
1. What PourCastAI is (and the problem it solves)
2. The core idea in one page
3. Real data vs simulated data (read this before the report)

**Part II — The agents (the "AI" part)**
4. Multi-agent architecture
5. The Inventory Agent
6. The Risk & Logistics Agent
7. The Orchestrator and the local LLM
8. Who built what

**Part III — Data and database**
9. Where the data comes from
10. The shared database and schema
11. How inventory and shipments are simulated from real sales

**Part IV — The enterprise layer (HubSpot + Databricks)**
12. Why we added HubSpot and Databricks
13. HubSpot (the CRM)
14. Databricks (the lakehouse)
15. How the cloud connects back to the local app
16. End-to-end data lineage

**Part V — Running it (the setup manual)**
17. Prerequisites
18. Path A — local-only quickstart
19. Path B — full enterprise setup (HubSpot + Databricks)
20. n8n automation (optional)
21. The `.env` file

**Part VI — Reference**
22. File-by-file map
23. Troubleshooting
24. Known assumptions and limitations
25. Glossary

---

# Part I — Understand the project

## 1. What PourCastAI is (and the problem it solves)

Iowa is a **control state**: the state government itself is the wholesaler for
spirits. Distillers/brand owners ("vendors") sell their product to the state, it
sits in a central state warehouse in **Ankeny, Iowa**, and from there it is shipped
out to retail liquor stores across all 99 counties. So the supply chain is literally:

```
Vendor (brand owner)  ->  Ankeny state warehouse  ->  retail store  ->  customer
```

Two things can go wrong in that chain, and both cost money:

1. **Stockout** — a store runs low on a product before its next delivery arrives, so
   it loses sales.
2. **Delivery risk** — the truck bringing the replenishment is likely to be late
   (bad weather, long rural route, expensive fuel, an unreliable carrier), so even a
   correctly-timed reorder doesn't help.

**PourCastAI is a decision-support assistant that watches both.** It answers
questions like *"Which stores are about to run out?"*, *"What's the highest-risk
shipment right now?"*, and *"How much inventory value is at risk this week?"* — in
plain English, from a chat box, using real data.

### Objectives (what "done" looks like)

- A **working demo**: a dashboard + chat UI that shows live inventory health and
  delivery-risk scoring for a slice of real Iowa stores/items.
- A credible **enterprise architecture story**: the system doesn't just run off one
  local file — it uses a real **CRM (HubSpot)** and a real **data lakehouse
  (Databricks + Delta Lake)**, the way an actual supply-chain company would.
- A **report + executive summary** that can honestly say which parts are real, which
  are simulated, and why every design choice was made.

## 2. The core idea in one page

There are **two AI agents** that share **one source of truth**:

- The **Inventory Agent** decides *which store-items are running low*. It reads the
  latest inventory snapshot, applies a statistical reorder-point model, and produces
  a list of **low-stock flags**.
- The **Risk & Logistics Agent** decides *how risky the incoming shipments are*. It
  takes open shipments and scores each one 0–100 using live weather, routing
  distance, fuel price, carrier reliability, and how rural the destination is.

These two agents are wired in a deliberate **sequential pipeline**:

```
Inventory Agent  ->  low-stock flags  ->  Risk Agent  ->  scored shipments  ->  LLM writes the answer
```

A local **Large Language Model (Ollama, running the `qwen2.5:7b-instruct` model)**
turns the agents' numbers into a readable answer. If the LLM isn't running, a
templated fallback answers instead, so the demo never breaks.

> **Important honesty point for the report:** this is a *fixed* pipeline — Inventory
> always runs, then Risk, then the LLM. It is **not** yet an LLM that dynamically
> decides which agent to "route" a question to. The UI shows a "Router Agent" tile,
> but that's a focus hint, not real routing. Tool-based routing is a documented next
> step, not a current capability. Represent it that way.

## 3. Real data vs simulated data (read this before the report)

This is the single most important thing to state clearly, because a grader will ask.

| Thing | Real or simulated? | Source |
|---|---|---|
| Sales transactions | **REAL** | Iowa Liquor Sales dataset (Kaggle) |
| Items / products (SKUs) | **REAL** | Iowa dataset |
| Stores + their GPS coordinates | **REAL** | Iowa dataset |
| Vendors (brand owners) | **REAL** | Iowa dataset |
| County population (rural flag) | **REAL** | US Census ACS5, fetched once at build time |
| Weather alerts, road hazards, forecast | **REAL (live)** | NWS + Open-Meteo APIs |
| Route distance / drive time | **REAL (live)** | OSRM routing API |
| Diesel price | **REAL (live)** | US EIA API |
| **Inventory levels (on-hand, days of cover)** | **SIMULATED** — but *derived from* real sales | `simulate.py` |
| **Reorder points / safety stock** | **SIMULATED** — statistical model | `simulate.py` |
| **Inbound shipments** | **SIMULATED** — one per reorder the sim triggers | `simulate.py` |
| Vendor lead times / reliability | **SIMULATED** (labelled) | random within realistic ranges |
| Carrier list | **Ruan is real; the rest simulated** | `dim_carrier` |

The key phrase to use: *"We never invent inventory out of thin air. We start each
store-item with a plausible opening stock, deplete it every day by the **real**
bottles sold, and place a replenishment order whenever stock crosses the reorder
point. Every simulated shipment exists because a real sale drained the shelf."*

---

# Part II — The agents (the "AI" part)

## 4. Multi-agent architecture

The pipeline is expressed two ways, both calling the *same* underlying functions:

- **`orchestrator.py`** — the "chat-optimized" path the UI actually uses. It runs
  Inventory + Risk **once per session** (cached), because those steps touch the
  database and live APIs and are the slow part. Then each chat message only re-runs
  the cheap LLM call. This is what makes follow-up questions feel instant.
- **`agent_graph.py`** — the "reference architecture" using **LangGraph**. It wraps
  each agent as an explicit graph node (`guardrail -> inventory -> risk -> answer`)
  so the multi-agent design is visible and diagrammable for the report
  (`get_graph().draw_ascii()`). It re-runs everything per question, so it's the clean
  textbook version, not the fast one.

The graph shape:

```
START ─▶ guardrail ─┬─▶ inventory ─▶ risk ─▶ answer ─▶ END
                    └─▶ answer ─▶ END        (out-of-scope question: skip the agents)
```

The **guardrail** runs first and deterministically declines questions the data can't
answer (subjective "what's the best vodka", hypotheticals, identity questions, false
"you said yesterday…" memory), so the LLM never fabricates an answer to a question
the data doesn't support.

## 5. The Inventory Agent

**File:** `inventory_agent.py`. **Reads:** the `gold_inventory_health` view/table.
**Produces:** low-stock flags.

Its job is narrow and reliable: return every store-item whose **days of cover** has
fallen at or below its **reorder point**, on the most recent snapshot day. Those
flags are the *only* thing handed to the Risk Agent — keyed by the shared
`(store_number, item_number)` — so the two agents can never disagree about what an
item or store is.

### The reorder-point model (the real logic worth explaining)

The naive approach is "reorder when you have less than 14 days of stock." We
**replaced** that flat rule with a statistical **(s, S) safety-stock model**, because
a flat day-count can't tell a steady seller apart from a wildly variable one — both
would get the same buffer even though the variable one needs more.

For each store-item we compute:

```
variance      = (lead_time_avg × demand_std²) + (demand_avg² × lead_time_std²)
safety_stock  = Z × √variance
reorder_point = demand_avg × lead_time_avg + safety_stock
```

- `demand_avg`, `demand_std` come from the **real** daily sales series (including
  zero-sale days, so variability is honest).
- `lead_time_avg` comes from the vendor; `lead_time_std` is assumed to be **20 %** of
  it (no public dataset tracks Iowa's real delivery-time variance — this is a
  documented assumption, sensitivity-tested in `sensitivity_check.py`).
- `Z = 1.645`, the standard-normal quantile for a **95 % service level** (i.e. we
  accept a 5 % chance of stocking out during a lead time). Hardcoded to avoid pulling
  in SciPy for one constant.

`sensitivity_check.py` re-runs this under Z = 90 %/95 %/99 % and lead-time-std =
10 %/20 %/30 % and reports how many items would be flagged under each, so the report
can say "we tested X, Y, Z and chose 95 %/20 % because …" instead of "we picked it."

## 6. The Risk & Logistics Agent

**File:** `risk_agent.py`. **Reads:** open shipments (`gold_shipments_open`) or the
pre-computed `gold_risk_scores` table. **Produces:** each shipment scored 0–100 with
a HIGH/MEDIUM/LOW band.

This is the project's signature piece. Each shipment's risk is a weighted blend of
**seven** factors, each normalised to 0–1 and then combined into a 0–100 score:

| Factor | Weight | Source | What it captures |
|---|---|---|---|
| Weather alert (destination) | 25 % | NWS point alerts | severe weather at the store |
| Road hazard (statewide) | 10 % | NWS Iowa alerts | corridor hazards (e.g. I-80 closed) |
| Precipitation forecast | 10 % | Open-Meteo | heavy-but-non-severe weather alerts miss |
| Distance | 20 % | OSRM routing | `min(distance_km / 300, 1)` |
| Diesel price | 10 % | EIA | `clamp((price − 3.50) / 1.50, 0, 1)` |
| Carrier reliability | 15 % | `dim_vendor` | `1 − reliability_score` |
| Rurality | 10 % | US Census | 1 if county population < 20,000 |

```
score = 100 × (0.25·weather + 0.10·road_hazard + 0.10·precip
             + 0.20·distance + 0.10·diesel + 0.15·reliability + 0.10·rural)

band  = HIGH   if score ≥ 60
        MEDIUM if score ≥ 30
        LOW    otherwise
```

The agent also **re-estimates the ETA**: if weather (alert or forecast) signals bad
conditions and we have a drive-time estimate, it stretches the promised delivery date
by `duration × (1 + weather_severity)`.

> **Design note (the "gated pipeline"):** the live `risk_agent.run()` only scores
> shipments whose `(store, item)` matches an inventory flag — Risk never runs before
> Inventory has something to check. This is the deliberate sequential design. It
> works cleanly when shipments are generated from the same simulation that produced
> the flags. It does **not** produce results when shipments come from a small,
> hand-seeded set (e.g. HubSpot Deals) that doesn't overlap the flagged items — which
> is exactly why the Databricks path uses the pre-computed `gold_risk_scores` table
> instead (see §15).

Every external call in `risk_tools.py` is wrapped so a blocked network, a
rate-limited server, or a missing coordinate **never crashes the demo** — it falls
back to a labelled estimate (e.g. straight-line haversine distance instead of OSRM)
and reports `is_live = False`, so the UI can honestly show "live" vs "fallback."
Results are cached per location, so scoring 194 shipments that share ~30 stores makes
~30 API calls, not 194.

## 7. The Orchestrator and the local LLM

**File:** `orchestrator.py`. This is the brain that ties the agents to the language
model.

- **Split into two calls.** `get_pipeline_data()` runs Inventory + Risk (slow: DB +
  APIs) once per session. `answer_question()` runs per chat message (fast: one LLM
  call). This caching is why the chat feels conversational instead of recomputing two
  tables on every message.
- **Risk source is chosen automatically** (added after the Databricks migration):
  - If a synced **`gold_risk_scores`** table is present locally (Databricks path),
    read those pre-computed scores directly — all open shipments, already scored, no
    live API calls at query time.
  - Otherwise, score the flagged reorders live via `risk_agent.run()` (local-sim
    path). The chosen path is reported as `risk_source` (`databricks_gold` vs
    `live_scoring`) and shown in the UI footer.
- **The LLM.** Ollama runs locally on port `11434` with the `qwen2.5:7b-instruct`
  model. The prompt is locked down: answer in **English only**, in 4–6 sentences,
  using **only** the supplied data, and never invent a supplier/price/number.
  (`qwen2.5` is a bilingual Chinese/English model and will drift into Chinese
  mid-answer if the language isn't pinned — hence the explicit English instruction
  and a low `temperature: 0.2`.)
- **Guardrails + fallback.** Out-of-scope questions are declined before the LLM is
  even called. If Ollama is unreachable, a question-aware **templated fallback**
  answers from the live data (it even filters to a named store number), so the demo
  survives with no model running.

## 8. Who built what

| Area | Owner |
|---|---|
| Risk & Logistics Agent, orchestration, overall architecture | **Sid** |
| Inventory Agent | **Vishnu** |
| Streamlit UI | **Sai Charan** |
| Market Analysis | **Gaurangi** |
| Demand Forecasting | **Tania** |

The team shares one database keyed on `item_number` and `store_number` as conformed
dimensions, seeded with `random.seed(42)` so every teammate's build is identical.

---

# Part III — Data and database

## 9. Where the data comes from

1. **The foundation — Iowa Liquor Sales (Kaggle), ~3.4 GB.** Real transactions:
   sales, items, stores (with GPS), vendors. You keep the whole file locally but
   never load it all — **DuckDB** scans the CSV with SQL and pulls only the busiest
   **25 items × 20 stores over the last 12 months** into the shared database. Resize
   via `TOP_N_ITEMS` / `TOP_M_STORES` / `MONTHS_BACK` in `build_database.py`.
2. **Five live external APIs (real, called at runtime by the Risk Agent):** NWS
   point alerts, NWS statewide road hazards, Open-Meteo precipitation forecast, OSRM
   routing, EIA diesel price. All free; only EIA needs a key.
3. **US Census (real, fetched once at build time):** county population → the
   `rural_flag`. Population doesn't change day-to-day, so there's no reason to call it
   per question the way weather/diesel are.
4. **Simulated (derived from real sales):** inventory snapshots, reorder points, and
   shipments — see §11.

### Data sources that did NOT work (state these as documented fallbacks)

- **Iowa DOT 511** is not an open API (requires a developer access request) →
  simulated road advisories used instead.
- **Iowa Socrata API** is network-blocked on campus → real CSV used as a documented
  fallback.
- **AISStream** (vessel tracking) is also campus-blocked → treated as a
  demonstrated-off-campus feature.
- **EIA** has high latency → solved cleanly with the n8n scheduled cache (see §20)
  rather than fighting it.

## 10. The shared database and schema

**File:** `schema.sql`. One SQLite file (`data/pourcast.db`), opened by both agents
through the single helper `db.py`. Because it's **one physical file with one set of
dimension tables**, the two agents can never disagree about what item 26827 or store
2633 is. That is what "shared database, no mismatch" means in practice.

**The two keys that tie everything together:** `item_number` (a product/SKU) and
`store_number` (a retail store). Every fact table references these same keys.

### Dimensions (the "things")

- **`dim_vendor`** — supplier of a product. Real vendor number/name; simulated
  `lead_time_days` (2–10), `lead_time_std` (20 % of lead time), `reliability_score`
  (0.85–0.99).
- **`dim_item`** — one row per SKU. Real: description, category, vendor, pack size,
  bottle volume, wholesale cost, retail price.
- **`dim_store`** — one row per store. Real: name, address, county, **latitude/
  longitude** (the Risk Agent routes Ankeny → these coords), population; derived
  `rural_flag`.
- **`dim_carrier`** — who drives the truck. Ruan is real; the others are simulated so
  the scorecard has something to compare.

### Facts (the "events")

- **`fact_sales`** — REAL transactions, one row per sale line.
- **`fact_inventory_snapshot`** — SIMULATED, one row per store × item × **day**;
  on-hand depleted each day by real sales, refilled by orders. This is the Inventory
  Agent's world.
- **`fact_shipments`** — SIMULATED inbound replenishment orders (Ankeny → store), one
  per reorder the sim places. This is the Risk Agent's world.

### Gold views — one ready-to-use join per agent

- **`gold_inventory_health`** — latest snapshot per store-item, with
  `inventory_value` and a `low_stock_flag`. The Inventory Agent reads this.
- **`gold_shipments_open`** — open inbound shipments joined to store/item/vendor/
  carrier, with `shipment_value`. The Risk Agent reads this.

### `ext_cache` — not part of the star schema

A tiny key/value cache (`cache_key`, `value`, `fetched_at`) that n8n writes into on a
schedule (e.g. diesel price every 30 min). `risk_tools.py` reads it **first**, before
calling the live API, so a chat question never blocks on a slow external API
mid-conversation. It's not wiped by `simulate.py`.

## 11. How inventory and shipments are simulated from real sales

**File:** `simulate.py`. Reads `fact_sales` + the dimensions; writes
`fact_inventory_snapshot` and `fact_shipments`.

For every store-item that actually has sales, over the full real date span:

1. Start with an **opening stock** of ~21 days of average demand (`OPENING_DAYS`).
2. Each day, receive any delivery due, then **deplete on-hand by the REAL bottles
   sold** that day.
3. Record a daily snapshot (on-hand, reorder point, safety stock, days of cover).
4. If on-hand falls **at or below** the reorder point and nothing is already in
   transit, place an order (~28 days of stock, `ORDER_DAYS`) with an ETA of
   `today + lead_time`, and write a shipment row. Origin is the real Ankeny warehouse
   (41.699, −93.558); destination is the store's real coordinates.

**Date shifting:** the Kaggle dates end years in the past. Depletion still uses the
real dates to look up real sales, but every date *written* to the DB is shifted by a
constant offset so the **last simulated day lands on today** — otherwise a shipment
ETA would read "2016" while you demo in 2026, and "days until late" would be
nonsensical.

---

# Part IV — The enterprise layer (HubSpot + Databricks)

## 12. Why we added HubSpot and Databricks

The project started fully local (one SQLite file). The professor's requirement then
evolved: make it **resemble a real supply-chain company's architecture**, with a real
**CRM** and a real **data warehouse/lakehouse**. So:

- **HubSpot** plays the role a CRM plays in a real company: it holds the **current
  operational picture** — which stores are customers, and which shipments are open
  right now — not a copy of all history. Sales/ops staff live in the CRM.
- **Databricks + Delta Lake** plays the role of the **lakehouse**: the analytical
  backbone where raw data lands, gets cleaned into conformed tables, and is served as
  ready-to-query gold tables. It's where the heavy joins and the risk scoring run at
  scale, on a schedule, instead of per chat message.

Crucially, we kept the **local Streamlit app as the demo front-end**, and made the
cloud feed *into* it (via a sync step, §15) so the demo doesn't depend on Databricks
Free Edition being reachable at demo time.

## 13. HubSpot (the CRM)

### What it holds

- **Companies** = the 20 tracked **stores** (one company per store, permanent).
- **Deals** = **currently open shipments** (re-seeded anytime; idempotent). A CRM
  holds the *current* operational picture, so only open shipments become Deals — full
  history stays in the lakehouse.
- A custom **deal pipeline** ("PourCastAI Distribution") with four stages:
  **Ordered → In Transit → Delivered → At Risk**.

### Why HubSpot specifically

Free tier, a clean REST API, and a real notion of pipelines/stages that maps
naturally onto a shipment's lifecycle. Seeding only establishes the *starting* state;
the "live CRM reflects agent decisions" story (moving a deal to **At Risk** when the
Risk Agent's score crosses threshold, or **Delivered** once the ETA passes) is a
documented next step.

### One-time HubSpot setup

1. Create a free HubSpot account.
2. Create a **private app** named `PourCastAI`. Give it CRM scopes:
   `crm.objects.companies` (read/write), `crm.objects.deals` (read/write),
   `crm.schemas.companies`, `crm.schemas.deals`.
3. Copy the private-app **access token** into `.env` as `HUBSPOT_ACCESS_TOKEN`.
4. Run `python hubspot_check_pipeline.py` — this verifies the token and prints your
   deal **pipeline id** and the four **stage ids** the seeder needs.

### The seeding flow (`hubspot_seed.py`)

`ensure_all_properties()` first creates the custom properties the objects need
(`store_number`, `item_number`, `shipment_id`, `quantity_bottles`, `order_date`,
`carrier_id`) — the free plan allows 10 custom properties, this uses well under that.
Then `seed_companies()` upserts one company per store, and `seed_deals()` upserts one
deal per **open** shipment, pulling from:

```sql
SELECT * FROM gold_shipments_open WHERE promised_eta >= date('now')
```

Everything is an **idempotent upsert** (search by `store_number`/`shipment_id`, then
PATCH or POST), so re-running after each `simulate.py` refresh updates records instead
of creating duplicates.

## 14. Databricks (the lakehouse)

Databricks organises data in a **medallion architecture** — three quality tiers,
each a schema inside the `pourcastai` **catalog**, all stored as **Delta Lake**
tables:

```
bronze  (raw, as-ingested)  ->  silver  (cleaned, conformed)  ->  gold  (business-ready)
```

Files uploaded to Databricks live in a **Volume** at
`/Volumes/pourcastai/bronze/landing`.

### Bronze — raw ingestion

Bronze holds raw pulls, one table per source:

- `hubspot_companies`, `hubspot_deals` — pulled from the HubSpot CRM.
- `osrm_routes`, `nws_point_alerts`, `weather_forecast`, `nws_road_hazards` — the
  live risk sources, fetched from within Databricks (network egress to OSRM/NWS/
  Open-Meteo was tested and works; EIA times out from Databricks compute, so diesel
  stays on the local n8n cache path and is written through as a CSV).

### Silver — cleaned, conformed (`06_silver_layer.py`)

Rebuilds the same dimensions/facts as `schema.sql`, from Bronze + the static CSVs you
uploaded:

- `dim_store` ← `bronze.hubspot_companies` (+ `store_coords.csv` for lat/long, since
  a CRM wouldn't hold GPS).
- `dim_item`, `dim_vendor`, `dim_carrier`, `fact_sales`, `fact_inventory_snapshot` ←
  straight loads of the exported CSVs (`export_static_tables.py`).
- `fact_shipments` ← `bronze.hubspot_deals`, with `vendor_number` recovered via a
  `dim_item` join and origin/dest coordinates derived (Ankeny fixed, dest from
  `dim_store`). (Because deals are the small open-shipments set, this is typically
  ~8 rows — remember that number, it matters in §15.)

### Gold — business-ready (`07_gold_layer.py`)

Three Delta tables:

- `gold_inventory_health` — same columns/logic as the SQL view.
- `gold_shipments_open` — same 4-table join as the SQL view.
- **`gold_risk_scores`** — the **real 7-factor risk formula from `risk_agent.py`,
  computed here in Spark for every open shipment**, joining in the Bronze risk
  sources + diesel. This pre-computes what the app used to do per chat turn.

The notebook's verify step checks that `reliability_risk`/`rural_risk` aren't all
null — a null there means an upstream join silently dropped rows.

## 15. How the cloud connects back to the local app

Databricks Free Edition has no uptime guarantee, so the demo must not depend on it
being live. The bridge is **`databricks_sync.py`**:

- It connects to your Databricks SQL Warehouse (needs `DATABRICKS_SERVER_HOSTNAME`,
  `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` in `.env`, and
  `pip install databricks-sql-connector`).
- It pulls the **three Gold Delta tables** and writes them into the local
  `pourcast.db` as **plain tables** (`gold_inventory_health`, `gold_shipments_open`,
  `gold_risk_scores`), replacing the old SQL *views* of the same name.
- It's a deliberate **snapshot**, not a live connection. Re-run it anytime you want
  fresher data — e.g. right before presenting.

### The critical connection: the app reads the Gold risk table

After the sync, the app's `orchestrator.get_pipeline_data()` **prefers
`gold_risk_scores`** if that table exists:

```
if a synced gold_risk_scores table is present  ->  use Databricks-computed scores  (risk_source = "databricks_gold")
else                                            ->  score flagged reorders live      (risk_source = "live_scoring")
```

**Why this matters (a real bug we fixed):** the live `risk_agent.run()` only scores
shipments whose `(store, item)` matches a low-stock flag. In the Databricks path,
shipments come from ~8 HubSpot Deals, which don't overlap the 11 flagged low-stock
items — so the gated live path would score **zero**, and the risk dashboard showed
all zeros. Reading `gold_risk_scores` directly fixes it: all open shipments show,
already scored, no live API calls, and the Databricks Gold layer becomes the actual
source of truth it was built to be.

> **Heads-up:** `databricks_sync.py` only pulls the **3 Gold tables**. The Catalog
> and item-detail views read `dim_item`/`dim_vendor`/`fact_inventory_snapshot`, which
> aren't synced — they work only if those tables still exist locally from an earlier
> `build_database.py` run. On a clean machine that *only* synced, extend the sync to
> pull those Silver tables too.

## 16. End-to-end data lineage

```
Iowa Kaggle CSV (3.4 GB, REAL)
        │  build_database.py  (DuckDB scans, pulls a slice)
        ▼
   pourcast.db  ──►  dim_vendor / dim_item / dim_store / dim_carrier / fact_sales
        │  simulate.py  (deplete by real sales, place reorders)
        ▼
   fact_inventory_snapshot + fact_shipments
        │
        ├─► export_store_coords.py  ─────────► store_coords.csv ─┐
        ├─► export_static_tables.py ─────────► dim_*.csv / fact_sales.csv / fact_inventory_snapshot.csv / diesel_price.csv ─┐
        │                                                                                                                    │
        │  hubspot_seed.py  (open shipments -> Deals, stores -> Companies)                                                   │
        ▼                                                                                                                    │
     HubSpot CRM (Companies + Deals) ──► Databricks BRONZE (hubspot_companies, hubspot_deals)                               │
     Live APIs from Databricks       ──► Databricks BRONZE (osrm_routes, nws_point_alerts, weather_forecast, nws_road_hazards)
     CSV uploads to Volume ──────────────────────────────────────────────────────────────────────────────────────────────┘
        │  06_silver_layer.py
        ▼
     Databricks SILVER (dims + facts, conformed)
        │  07_gold_layer.py
        ▼
     Databricks GOLD (gold_inventory_health, gold_shipments_open, gold_risk_scores)
        │  databricks_sync.py  (pull 3 gold tables back down)
        ▼
   pourcast.db  (gold_* now local tables)
        │  orchestrator.get_pipeline_data()  ── prefers gold_risk_scores ──► Inventory + Risk data
        ▼
   Streamlit app.py  ──►  Ollama (qwen2.5:7b-instruct)  ──►  chat answer + dashboards
```

---

# Part V — Running it (the setup manual)

There are two ways to run PourCastAI. **Path A (local-only)** is the fastest way to
see it working. **Path B (enterprise)** adds HubSpot + Databricks. Do Path A first.

## 17. Prerequisites

- **Python 3.10+**
- **Node.js LTS** — only if you want n8n (Path B / automation).
- **Ollama** — the local LLM runtime. Install from ollama.com.
- The **Iowa Liquor Sales CSV** from Kaggle, placed at `data/raw/Iowa_Liquor_Sales.csv`
  (Path A only — Path B can run off the exported CSVs instead).
- Accounts (Path B only): a free **HubSpot** account and a free **Databricks** account.

> Development was on **Windows/PowerShell**; commands below note where PowerShell
> syntax differs from bash.

## 18. Path A — local-only quickstart

### Step 1 — Create and activate a virtual environment

From the project folder (`pourcast-ai/`):

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. **Everything below runs inside this
activated venv.**

> If PowerShell blocks the activate script, run once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### Step 2 — Install dependencies

```bash
pip install -r requirements.txt
```

(That's DuckDB, pandas, requests, python-dotenv, Streamlit, Altair, LangGraph.)

### Step 3 — Create your `.env`

Copy `.env.example` to `.env`. For Path A you can leave the keys blank — the app
falls back gracefully — but an `EIA_API_KEY` gets you a live diesel price. See §21.

> **Security:** the shipped `.env.example` was found to contain real keys once.
> **Rotate any key that was ever committed**, and make sure `.env` (and `data/`) are
> in `.gitignore`. Never commit tokens.

### Step 4 — Build the database (one-time / whenever data changes)

```bash
python build_database.py     # DuckDB scans the CSV -> dims + real fact_sales
python simulate.py           # depletion/reorder -> inventory snapshots + shipments
```

You should see row counts printed, ending with a preview of the low-stock items.

### Step 5 — Install the LLM

```bash
ollama pull qwen2.5:7b-instruct
# make sure Ollama is running (the app checks http://localhost:11434)
```

On a slow CPU, `qwen2.5:3b-instruct` is a lighter alternative — set it without
touching code via `OLLAMA_MODEL` (see §21). The *first* answer after startup can take
60–120 s while the model loads into RAM; that's normal.

### Step 6 — (optional) Sanity checks

```bash
python inventory_agent.py        # prints the low-stock list
python risk_agent.py             # prints scored routes
python check_data_sources.py     # PASS/FALLBACK/FAIL for every live API — run before a demo
```

### Step 7 — Run the app

```bash
streamlit run app.py
```

It opens at `http://localhost:8501`. Use the left rail to switch pages (Dashboard,
Shipments, Inventory, Risk Monitoring, CRM, Reports, Alerts, Settings) and the right
rail to chat. Click **Refresh data** after any rebuild.

## 19. Path B — full enterprise setup (HubSpot + Databricks)

Do Path A first (you need the local DB to export from). Then:

### Step 1 — HubSpot

1. Create the free account + private app `PourCastAI` with the CRM scopes (§13).
2. Put `HUBSPOT_ACCESS_TOKEN` in `.env`.
3. `python hubspot_check_pipeline.py` — confirms the token, prints pipeline/stage ids.
4. `python hubspot_seed.py` — creates Companies (stores) and Deals (open shipments).

### Step 2 — Export the static/reference data for Databricks

```bash
python export_store_coords.py     # -> store_coords.csv (lat/long a CRM wouldn't hold)
python export_static_tables.py    # -> dim_item.csv, dim_vendor.csv, dim_carrier.csv,
                                  #    fact_sales.csv, fact_inventory_snapshot.csv, diesel_price.csv
```

### Step 3 — Databricks

1. Create a free Databricks workspace. Create a **catalog** `pourcastai` with schemas
   `bronze`, `silver`, `gold`, and a **Volume** at
   `/Volumes/pourcastai/bronze/landing`.
2. **Upload** the CSVs from Step 2 into that Volume.
3. Ingest HubSpot + the live risk APIs into Bronze (`hubspot_companies`,
   `hubspot_deals`, `osrm_routes`, `nws_point_alerts`, `weather_forecast`,
   `nws_road_hazards`).
4. Run **`06_silver_layer.py`** → builds the 6 Silver tables. Verify
   `fact_shipments` has its `vendor_number` and `dest_lat`/`dest_long` filled in.
5. Run **`07_gold_layer.py`** → builds `gold_inventory_health`,
   `gold_shipments_open`, and `gold_risk_scores`. Verify `risk_score` is 0–100 and
   the reliability/rural risk columns aren't all null.

### Step 4 — Sync the Gold tables back down

```bash
pip install databricks-sql-connector
# add DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN to .env
python databricks_sync.py
```

This copies the 3 Gold tables into `pourcast.db`. Now `streamlit run app.py` will
show **risk source: Databricks Gold** in the footer and the risk dashboard will
populate from your lakehouse scores.

## 20. n8n automation (optional)

n8n is a self-hosted workflow tool that moves the *scheduling* and *API polling* out
of the Python app. Run it natively (no Docker needed):

```powershell
$env:NODES_EXCLUDE = "[]"    # re-enables the Execute Command node for a local instance
npx n8n
# open http://localhost:5678, create a local admin (first run only)
```

Import the two workflows from `n8n/workflows/` and point their Execute Command nodes
at your venv's Python:

- **`nightly_rebuild.json`** — cron `0 2 * * *`: runs `build_database.py` then
  `simulate.py` so the demo data refreshes itself overnight.
- **`api_cache_refresh.json`** — every 30 min: runs `n8n/cache_refresh.py`, which
  writes the live EIA diesel price into `ext_cache`. `risk_tools.get_diesel_price()`
  reads that cache first (≤90 min old = fresh), so a chat question never blocks on the
  slow EIA API — only n8n's background schedule does.

> **Why `NODES_EXCLUDE="[]"`:** n8n 2.0 disables Execute Command by default (unsafe on
> a shared cloud instance). It's fine to re-enable on a single-user local instance;
> without it, importing these workflows fails with `Unrecognized node type`.

## 21. The `.env` file

Copy `.env.example` → `.env` and fill what you need. All are optional for Path A;
HubSpot/Databricks keys are needed for Path B.

```bash
# --- LLM (optional overrides) ---
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct     # or qwen2.5:3b-instruct on a slow CPU

# --- live diesel price (optional; falls back to a static estimate) ---
EIA_API_KEY=

# --- county population at build time (optional; keyless works but is throttled) ---
CENSUS_API_KEY=

# --- HubSpot (Path B) ---
HUBSPOT_ACCESS_TOKEN=

# --- Databricks (Path B) ---
DATABRICKS_SERVER_HOSTNAME=
DATABRICKS_HTTP_PATH=
DATABRICKS_TOKEN=
```

---

# Part VI — Reference

## 22. File-by-file map

| File | What it does |
|---|---|
| `schema.sql` | The shared DB contract: dims, facts, gold views, `ext_cache`. |
| `db.py` | The one place that opens `data/pourcast.db`. |
| `build_database.py` | DuckDB scans the Kaggle CSV → dims + real `fact_sales`; fetches Census population once. |
| `simulate.py` | Deplete by real sales, place reorders → `fact_inventory_snapshot` + `fact_shipments`. Date-shifts so "today" = last day. |
| `inventory_agent.py` | Reads `gold_inventory_health` → low-stock flags; catalog + item detail. |
| `risk_tools.py` | Live APIs (NWS, OSRM, Open-Meteo, EIA) with safe fallbacks + `ext_cache` read. |
| `risk_agent.py` | Scores flagged routes 0–100 (7 factors); `read_gold_scores()` reads the Databricks table. |
| `orchestrator.py` | Cacheable pipeline + Ollama (English-pinned, low-temp) + guardrails + fallback + risk-source switch. |
| `agent_graph.py` | LangGraph reference wiring (`guardrail → inventory → risk → answer`) for the report. |
| `app.py` | The Streamlit UI (dashboard shell, KPI cards, donuts, chat rail, data-health banner). |
| `sensitivity_check.py` | Justifies the Z=95 % / lead-std=20 % assumptions. |
| `check_data_sources.py` | PASS/FALLBACK/FAIL report for every live source — run before a demo. |
| `hubspot_check_pipeline.py` | Verifies the HubSpot token; prints pipeline/stage ids. |
| `hubspot_seed.py` | Seeds Companies (stores) + Deals (open shipments); idempotent upsert. |
| `export_store_coords.py` | Exports store lat/long → `store_coords.csv` for Databricks. |
| `export_static_tables.py` | Exports dims + `fact_sales` + inventory snapshot + diesel → CSVs. |
| `06_silver_layer.py` | Databricks: Bronze + CSVs → conformed Silver tables. |
| `07_gold_layer.py` | Databricks: Silver + Bronze risk → 3 Gold Delta tables incl. `gold_risk_scores`. |
| `databricks_sync.py` | Pulls the 3 Gold tables into local `pourcast.db`. |
| `docker-compose.yml`, `n8n/` | Optional automation layer. |

## 23. Troubleshooting

**Dashboard shows all zeros for risk (but inventory has data).**
Your risk data isn't reaching the app. If you're on Databricks, run
`python databricks_sync.py` so `gold_risk_scores` lands locally — the app prefers it
and the dashboard will populate. If you're purely local, your `fact_shipments` is
empty; re-run `python simulate.py`. Quick check:
`SELECT COUNT(*) FROM gold_risk_scores;` and `... FROM fact_shipments;` should be > 0.
The app now shows an explicit banner explaining which case you're in, instead of
silent zeros.

**The chatbot answers in Chinese.**
`qwen2.5` is a bilingual model that drifts into Chinese when the output language isn't
pinned. The prompt now says "respond in English only" (at the start *and* end) and
uses `temperature: 0.2`. If it still drifts, stay on the 7B model (the 3B drifts
more), or set a different `OLLAMA_MODEL`.

**The chatbot invents a supplier/price.**
The prompt now includes the real vendor per item and instructs "never invent facts not
in the data." Make sure you're on the updated `orchestrator.py`.

**Streamlit crashes on shutdown with `'Server' object has no attribute 'servers'`.**
That's a Streamlit 1.53+ / uvicorn teardown bug, not your code — it only fires on
Ctrl+C or file-save reload. Pin a Tornado-based build (`pip install "streamlit==1.50.0"`);
the app is written to run on it.

**Catalog / item-detail page errors after a fresh Databricks sync.**
`databricks_sync.py` only pulls the 3 Gold tables; the catalog needs
`dim_item`/`dim_vendor`/`fact_inventory_snapshot`. Either keep those from a
`build_database.py` run, or extend the sync to pull them from Silver.

**First chat answer takes a minute.**
Normal — Ollama is loading the 7B model into RAM. `keep_alive: 30m` keeps it warm, so
only the first message in a session is slow.

## 24. Known assumptions and limitations

- **`lead_time_std` = 20 % of `lead_time_days`** — no public dataset tracks Iowa's
  real vendor delivery variance. Documented and sensitivity-tested.
- **Rural = county population < 20,000** — a documented simplification, not the
  official USDA Rural-Urban Continuum Code.
- **Vendor reliability + carrier list are simulated** and labelled as such.
- **The pipeline is fixed and sequential, not LLM-routed** — the "Router Agent" is a
  focus hint; tool-based routing is a next step.
- **Shipment "status" (In Transit / Delayed / At Risk)** shown on the dashboard is
  *derived* from the risk band + ETA slip, because there is no live status field.
- **HubSpot Deals hold only open shipments**, seeded at a point in time — they don't
  necessarily overlap the current low-stock flags, which is why the app reads the
  pre-computed `gold_risk_scores` rather than re-gating on flags in the Databricks
  path.
- **Databricks Free Edition has no uptime SLA**, so the demo runs off a **synced
  snapshot**, not a live connection.

## 25. Glossary

- **Control state** — a US state that is itself the wholesaler of spirits (Iowa is
  one). The state warehouse in Ankeny is the single distribution hub.
- **Reorder point (s, S model)** — the stock level at which you place an order,
  sized to both demand variability and lead-time variability, not a flat day count.
- **Days of cover** — on-hand bottles ÷ average daily demand: how long current stock
  lasts.
- **Medallion architecture** — Bronze (raw) → Silver (cleaned/conformed) → Gold
  (business-ready), the standard lakehouse layering.
- **Delta Lake** — the transactional table format Databricks Gold/Silver/Bronze
  tables are stored in.
- **Conformed dimension** — a dimension table (like `dim_store`) shared by every
  agent/fact so nobody disagrees about what a store or item is.
- **`is_live` / fallback** — every external call reports whether it got live data or
  a labelled estimate, so the UI never silently shows stale numbers as fresh.
- **Idempotent upsert** — a write that can be re-run safely: it updates the existing
  record instead of creating a duplicate.

---

*End of manual. If a section is unclear, the source file named in it is the ground
truth — every claim here is traceable to the code.*
