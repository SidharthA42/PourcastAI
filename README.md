# PourCastAI

A local, multi-agent stocking & delivery-risk assistant for Iowa liquor
distribution. Two agents share one database and answer natural-language
questions about stockout risk and inbound-shipment risk.

## Folder structure

```
pourcast-ai/
├── data/
│   ├── raw/
│   │   └── Iowa_Liquor_Sales.csv     <- the 3.4 GB Kaggle file (you add this)
│   └── pourcast.db                   <- shared SQLite DB (generated)
│
├── schema.sql            # STEP 1  the shared schema (the contract)
├── build_database.py     # STEP 2-4 DuckDB scans CSV -> dims + real fact_sales
├── simulate.py           # STEP 5  depletion/reorder -> inventory + shipments
│
├── db.py                 # one place that opens the shared DB
├── inventory_agent.py    # Vishnu: reads gold_inventory_health -> low-stock flags
├── risk_tools.py         # live APIs (NWS, OSRM, EIA) with safe fallbacks + cache read
├── risk_agent.py         # Sid: scores flagged routes 0-100, re-estimates ETA
├── orchestrator.py       # pipeline (cacheable) + local Ollama (with fallback)
├── app.py                # Sai Charan: Streamlit UI - Chat tab + Dashboard tab
│
├── n8n/
│   ├── cache_refresh.py         # writes live diesel price into ext_cache
│   └── workflows/
│       ├── nightly_rebuild.json      # import into n8n: rebuild DB @ 2AM
│       └── api_cache_refresh.json    # import into n8n: diesel price every 30 min
├── docker-compose.yml    # self-hosted n8n, project folder mounted in
│
├── requirements.txt
├── .env.example          # copy to .env, add EIA key
└── README.md
```

## Do NOT commit the raw CSV
It is 3.4 GB. Add `data/` to `.gitignore`. Keep the full file locally as your
real source; `build_database.py` only pulls a small slice into `pourcast.db`.

## The 3.4 GB question
Keep the whole file — but you never load it all. DuckDB reads the CSV directly
with SQL and materialises only the busiest 25 items × 20 stores over the last
12 months into the shared DB. Change `TOP_N_ITEMS` / `TOP_M_STORES` /
`MONTHS_BACK` at the top of `build_database.py` to resize the slice.

## Run order

```bash
pip install -r requirements.txt

# 1. build the shared database (one-time / whenever data changes)
python build_database.py
python simulate.py

# 2. optional sanity checks
python inventory_agent.py
python risk_agent.py

# 3. the UI
streamlit run app.py
```

For the LLM: install Ollama, then `ollama pull qwen2.5:7b-instruct`. If Ollama
isn't running, the orchestrator falls back to a templated answer so the demo
never breaks.

## Real vs simulated (state this in the report)
- REAL: sales, items, stores, vendors, store coordinates — all from the Iowa
  dataset. Live weather/routing/diesel when the network allows.
- SIMULATED (but derived from real sales): inventory levels, reorder points,
  and inbound shipments. Every shipment exists because a real sale drained the
  shelf; orders ship from the real Ankeny state warehouse to real store
  coordinates. Vendor lead-times/reliability and the carrier list are simulated
  and labelled as such.

## Reducing workload — automation tools
Your architecture is already using three "do the heavy lifting for you" tools;
here is where each fits, cheapest-effort first.

**DuckDB** (already wired in) — turns a slow multi-pass pandas scan of 3.4 GB
into one fast SQL query. This is the biggest single workload win in the data
layer.

**LangGraph** — instead of hand-wiring the agents, express the pipeline as a
graph: `inventory_node -> risk_node -> synthesis_node`. Each of your existing
functions (`inventory_agent.get_low_stock`, `risk_agent.run`) becomes a node.
It removes the glue code and gives you a clean diagram for the report.

**n8n** (what your professor asked about) — a self-hosted, open-source workflow
automation tool. Runs locally via Docker, so it fits the "fully local /
data stays in-house" security property (unlike a cloud scheduler). Use it as
the *outer* automation layer so the app itself does less work:

- A **Schedule (cron) trigger** runs the data refresh on its own — e.g. nightly
  `Execute Command` nodes that run `build_database.py` then `simulate.py`.
- **HTTP Request** nodes pull NWS alerts and EIA diesel on a schedule and cache
  them into a small table, so the agents read fresh-but-cached values instead
  of calling the APIs on every user query. That cuts per-question latency and
  fixes the "stale data" labelling problem — the cache has a known timestamp.
- n8n has native **Ollama** and MCP nodes, so you can even run the whole
  pipeline as a webhook the Streamlit UI calls.

Net effect: the batch build, the API polling, and the scheduling all move out
of your Python app and into n8n, which is exactly the workload reduction the
brief is after. Databricks Workflows (in your original doc) does the same job
but is cloud-oriented; n8n is the lighter, local-friendly choice for a demo.

### Running n8n

n8n's current Docker image ships without `apk`/`apt-get` at all (a known,
currently-open packaging issue on their side — see
[n8n-io/n8n#23603](https://github.com/n8n-io/n8n/issues/23603)), which is
why installing Python into it kept failing no matter how we tried. Two
options, in order of how much hassle they are:

**Option A — run n8n natively (recommended, no Docker at all)**

```bash
$env:NODES_EXCLUDE = "[]"   # re-enables Execute Command - see note below
npx n8n
# open http://localhost:5678, create a local admin account (first run only)
```

Requires Node.js (if `npx` isn't recognized, install Node.js LTS first).
This runs n8n as a normal process on your machine — no container, no base
image to fight with.

> **Why the `NODES_EXCLUDE` line:** n8n 2.0 disables the Execute Command
> (and LocalFileTrigger) node by default, since on a shared/cloud n8n
> instance it lets anyone run arbitrary shell commands. That risk doesn't
> apply to a single-user local instance, so it's safe to re-enable here —
> without it, importing these workflows fails with `Unrecognized node type:
> n8n-nodes-base.executeCommand`. Set it in every new PowerShell session
> before running `npx n8n`, or add it as a permanent user environment
> variable via `setx NODES_EXCLUDE "[]"` (System Properties → Environment
> Variables also works) so you don't have to retype it each time.

Import both workflows (**Workflows → Import from File**, load each `.json`
from `n8n/workflows/`), but before activating them, open each **Execute
Command** node and change the command to point at your venv's Python
directly, e.g.:

```
D:\pourcast-ai\.venv\Scripts\python.exe D:\pourcast-ai\build_database.py
```

(swap in your actual project path). That's it — since n8n is now a normal
Windows process, its Execute Command nodes run in a normal Windows shell
with access to your exact venv.

**Option B — Docker (more setup, optional)**

```bash
docker compose build
docker compose up -d
```

`n8n/Dockerfile` now builds on the `-debian` image tag (which still has
`apt-get`, unlike the default distroless-ish `latest` tag) and installs
Python + `requirements.txt` at build time. If `docker ps` still shows
`Restarting`, run `docker compose build` again and read the build log —
with Option B, image-level package installs fail loudly at build time
instead of crash-looping at runtime, so the log will say exactly what broke.
The imported workflows' Execute Command nodes can stay as `python3 ...` for
this option, since Python lives inside the container.

---

Once running (either option), import both workflows:

- **nightly_rebuild.json** — cron `0 2 * * *`, runs `build_database.py` then
  `simulate.py` against the shared `pourcast.db` so the demo data refreshes
  itself overnight with no manual step.
- **api_cache_refresh.json** — runs every 30 minutes, calls
  `n8n/cache_refresh.py`, which writes the live EIA diesel price into a new
  `ext_cache` table. `risk_tools.get_diesel_price()` now checks that table
  first (≤90 min old = fresh) before ever calling the EIA API directly — so a
  chat question never blocks on that API mid-conversation, only n8n's
  background schedule does.

Click each workflow's **Active** toggle to turn on the schedule. Both nodes
run `python3` inside the n8n container against `/project`, which is this
folder mounted read/write by `docker-compose.yml` — same `pourcast.db` your
Streamlit app reads, so no sync step is needed.

To extend this further (documented as a next step, not yet built): add an
`HTTP Request` node before `cache_refresh.py` for NWS state-wide alerts, or a
`Slack`/`Email` node after the nightly rebuild to notify the team if a run
fails (n8n's `Execute Command` node exposes a non-zero exit code you can
branch on with an `IF` node).
