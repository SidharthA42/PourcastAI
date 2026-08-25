# PourCastAI

**A multi-agent AI decision-support system for Iowa liquor supply-chain management.**

PourCastAI reads real Iowa liquor sales, simulates the inventory and shipments those
sales imply, scores every inbound shipment for delivery risk using live weather /
routing / fuel data, and answers plain-English questions about it — with a local LLM
writing the answers. It runs entirely on your machine, but is wired through a real
**CRM (HubSpot)** and a real **lakehouse (Databricks + Delta Lake)** the way an actual
distributor's stack would be.

> Iowa is a *control state*: the state warehouse in **Ankeny** ships spirits to retail
> stores across all 99 counties. Two things cost money there — **stockouts** (a store
> runs dry) and **late deliveries** (the replenishment truck is delayed). PourCastAI
> watches both.

📖 **Full documentation:** [`docs/PourCastAI_Manual.md`](docs/PourCastAI_Manual.md) —
a complete, read-it-start-to-finish manual (objectives, agents, schema, HubSpot,
Databricks, setup, troubleshooting). This README is the quickstart.

---

## What it does

- **Inventory Agent** — flags store-items running low, using a statistical (s, S)
  reorder-point model (demand + lead-time variability, 95% service level).
- **Risk & Logistics Agent** — scores each inbound shipment 0–100 from **7 live
  factors**: weather alerts, road hazards, precip forecast, route distance, diesel
  price, carrier reliability, and rurality.
- **Orchestrator + local LLM** — runs Inventory → Risk → a local **Ollama** model
  (`qwen2.5:7b-instruct`) that turns the numbers into a readable answer. Falls back to
  a templated answer if the model is offline, so the demo never breaks.
- **Streamlit dashboard + chat** — KPI cards, risk/status donuts, recent shipments,
  top risk factors, and a docked AI assistant.

The two agents share **one SQLite database** keyed on `store_number` / `item_number`,
so they can never disagree about what a store or item is.

---

## Quickstart (local-only)

```bash
# 1. virtual environment
python -m venv .venv
# Windows:  .\.venv\Scripts\Activate.ps1
# macOS/Linux:  source .venv/bin/activate

# 2. dependencies
pip install -r requirements.txt

# 3. env file (copy the template, fill in keys as needed — all optional locally)
cp .env.example .env

# 4. build the data (place the Kaggle CSV at data/raw/Iowa_Liquor_Sales.csv first)
python build_database.py     # DuckDB scans the CSV -> dims + real fact_sales
python simulate.py           # deplete by real sales -> inventory + shipments

# 5. the local LLM
ollama pull qwen2.5:7b-instruct   # Ollama must be running (localhost:11434)

# 6. run it
streamlit run app.py         # opens http://localhost:8501
```

First chat answer after startup takes ~1 min while the model loads into RAM — that's
normal. On a slow CPU, set `OLLAMA_MODEL=qwen2.5:3b-instruct` in `.env`.

---

## The two run paths

**Path A — local-only** (the quickstart above). Everything runs off `data/pourcast.db`.
Risk is scored live at query time.

**Path B — full enterprise** (HubSpot + Databricks). Adds the CRM and lakehouse layers,
then syncs the cloud-computed risk scores back to the local app:

```bash
# HubSpot (CRM)
python hubspot_check_pipeline.py    # verify token, print pipeline/stage ids
python hubspot_seed.py              # stores -> Companies, open shipments -> Deals

# export reference data for Databricks (CSVs land in the project root — move them
# into databricks/exports/ and upload to your Databricks Volume)
python export_store_coords.py
python export_static_tables.py

# in Databricks: upload CSVs + ingest HubSpot/live APIs to Bronze, then run
#   databricks/notebooks/06_silver_layer.py   -> conformed Silver tables
#   databricks/notebooks/07_gold_layer.py     -> gold_inventory_health,
#                                                 gold_shipments_open, gold_risk_scores

# pull the Gold tables back into the local app
pip install databricks-sql-connector
python databricks_sync.py           # needs DATABRICKS_* keys in .env
streamlit run app.py                # footer now shows "risk: Databricks Gold"
```

**How the app picks its risk source (automatic):** if a synced `gold_risk_scores`
table is present, the app reads those pre-computed scores (`risk_source =
databricks_gold`); otherwise it scores flagged reorders live (`risk_source =
live_scoring`). The rail footer shows which one is active.

See the [manual](docs/PourCastAI_Manual.md) (Parts IV–V) for the full HubSpot +
Databricks walkthrough.

---

## Project structure

Core Python modules and `schema.sql` stay at the **root** (they import each other by
name and expect to run from here). Only docs, notebooks, exports, and automation are
foldered.

```
pourcast-ai/
├── .env.example            # committed template (real .env is gitignored)
├── .gitignore
├── README.md
├── requirements.txt
├── docker-compose.yml      # n8n; mounts the project as /project
├── schema.sql              # the shared DB contract
│
├── app.py                  # Streamlit UI (dashboard + chat)
├── orchestrator.py         # pipeline + LLM + guardrails + risk-source switch
├── agent_graph.py          # LangGraph reference wiring
├── inventory_agent.py      # low-stock flags (reorder-point model)
├── risk_agent.py           # 7-factor risk scoring + read_gold_scores()
├── risk_tools.py           # live APIs (NWS, OSRM, Open-Meteo, EIA) + fallbacks
├── db.py                   # opens data/pourcast.db
├── build_database.py       # DuckDB: Kaggle CSV -> dims + fact_sales
├── simulate.py             # inventory snapshots + shipments from real sales
├── hubspot_check_pipeline.py
├── hubspot_seed.py         # seed Companies + Deals
├── export_static_tables.py
├── export_store_coords.py
├── databricks_sync.py      # pull Gold tables into local SQLite
├── check_data_sources.py   # PASS/FALLBACK/FAIL for every live API
├── sensitivity_check.py    # justifies the reorder-model assumptions
│
├── data/                   # gitignored
│   ├── raw/Iowa_Liquor_Sales.csv   # 3.4 GB Kaggle file (you add this)
│   └── pourcast.db                 # generated
│
├── docs/
│   ├── PourCastAI_Manual.md        # the full manual
│   └── PROJECT_DOCUMENTATION.md    # older local-only doc (superseded)
│
├── databricks/
│   ├── notebooks/
│   │   ├── 06_silver_layer.py
│   │   └── 07_gold_layer.py
│   └── exports/            # CSVs uploaded to the Databricks Volume
│
└── n8n/
    ├── cache_refresh.py
    └── workflows/
        ├── nightly_rebuild.json
        └── api_cache_refresh.json
```

---

## The 3.4 GB question

Keep the whole Kaggle file locally, but never load it all. **DuckDB** reads the CSV
directly with SQL and materialises only the busiest **25 items × 20 stores over the
last 12 months** into `pourcast.db`. Resize via `TOP_N_ITEMS` / `TOP_M_STORES` /
`MONTHS_BACK` at the top of `build_database.py`. **Never commit the CSV or the DB** —
`data/` is gitignored.

---

## Real vs simulated 

| Real | Simulated (derived from real sales) |
|---|---|
| Sales, items, stores (+ GPS), vendors — from the Iowa dataset | Inventory levels, days of cover |
| County population / rural flag — US Census (build-time) | Reorder points, safety stock |
| Weather, road hazards, forecast, routing, diesel — live APIs | Inbound shipments (one per reorder) |
| Ankeny warehouse coordinates | Vendor lead-times & reliability; carrier list (Ruan is real) |

We never invent inventory: each store-item starts with a plausible opening stock, is
depleted daily by the **real** bottles sold, and orders a replenishment when stock
crosses the reorder point. **Every simulated shipment exists because a real sale
drained a shelf.**

> **HubSpot & Databricks are real tools**, but the shipment/inventory data flowing
> through them is simulated-from-real-sales. The pipeline is a fixed Inventory → Risk →
> LLM sequence — **not** dynamic LLM routing (the "Router Agent" tile is a focus hint;
> tool-based routing is a documented next step).

---

## Environment & secrets

Copy `.env.example` → `.env` and fill in what you need. Everything is optional for
Path A; HubSpot/Databricks keys are needed for Path B.

```bash
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct     # qwen2.5:3b-instruct on a slow CPU
EIA_API_KEY=                          # live diesel price (optional)
CENSUS_API_KEY=                       # county population at build time (optional)
HUBSPOT_ACCESS_TOKEN=                 # Path B
DATABRICKS_SERVER_HOSTNAME=           # Path B
DATABRICKS_HTTP_PATH=                 # Path B
DATABRICKS_TOKEN=                     # Path B
```

**Never commit `.env`.** It's gitignored. If it was ever committed, deleting it now
doesn't remove it from git history — rotate those keys, and scrub history (BFG /
`git filter-repo`) if the repo is shared. To untrack a currently-tracked `.env`:
`git rm --cached .env`.

---

## Optional: automation (n8n)

n8n moves the scheduling and API polling out of the app. Run it natively (no Docker):

```powershell
$env:NODES_EXCLUDE = "[]"   # re-enables Execute Command on a local instance
npx n8n                     # http://localhost:5678
```

Import both workflows from `n8n/workflows/` and point their Execute Command nodes at
your venv's Python:

- **nightly_rebuild** — cron `0 2 * * *`: re-runs `build_database.py` + `simulate.py`.
- **api_cache_refresh** — every 30 min: caches the live diesel price into `ext_cache`,
  so chat questions never block on the slow EIA API.

---

## Troubleshooting (quick hits)

| Symptom | Fix |
|---|---|
| Risk dashboard all zeros, inventory has data | Run `python databricks_sync.py` (Databricks) or re-run `simulate.py` (local). The app shows a banner explaining which case. |
| Chatbot answers in Chinese | `qwen2.5` drifts without an English pin — use the updated `orchestrator.py` (English-pinned, `temperature 0.2`); stay on 7B. |
| Streamlit crashes on Ctrl+C/reload (`'Server' has no attribute 'servers'`) | Streamlit 1.53+/uvicorn teardown bug — `pip install "streamlit==1.50.0"`. |
| Catalog page errors after a fresh sync | `databricks_sync.py` only pulls the 3 Gold tables; the catalog needs the Silver dims — keep them from a `build_database.py` run or extend the sync. |
| First chat answer is slow (~1 min) | Normal — Ollama loading the model. It stays warm for 30 min after. |

Run `python check_data_sources.py` before any demo to confirm every live API is up.

---

## Team

| Area | Owner |
|---|---|
| Risk & Logistics Agent, orchestration, architecture | Sid |
| Inventory Agent | Vishnu |
| UI | Sai Charan |
| Databricks & architecture | Gaurangi & Tania|

---

## Tech stack

Python · SQLite · DuckDB · LangGraph · Streamlit · Altair · Ollama (`qwen2.5:7b-instruct`)
· HubSpot CRM · Databricks + Delta Lake · n8n · NWS / OSRM / Open-Meteo / EIA / US Census APIs
