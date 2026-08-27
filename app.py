"""
app.py  -  PourCastAI control tower (premium restyle).

Run:  streamlit run app.py

Same backend contract as before (orchestrator + inventory_agent). This revision:
  - Premium, emoji-free visual language (line-icon SVGs, refined palette/typography).
  - Distinct Shipments (operational list) vs Risk Monitoring (analytics).
  - A wired Alerts centre generated from live data, with acknowledge + thresholds.
  - A Settings page that reads/writes .env (API keys, model) and live thresholds.
  - Honest agent model: a read-only pipeline-status panel + a single assistant with
    a real "scope" control (no fake per-agent chat routing).
"""
import os
import datetime as dt
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import orchestrator
import inventory_agent

st.set_page_config(page_title="PourCastAI", layout="wide", initial_sidebar_state="expanded")

# ==========================================================================
# Icon set (inline line SVGs, currentColor) - replaces all emojis
# ==========================================================================
_ICON_PATHS = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>',
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "truck": '<rect x="1" y="3" width="15" height="13" rx="1.5"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
    "box": '<path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "report": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    "settings": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "alert": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "check": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/>',
    "dollar": '<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "trenddown": '<polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "bottle": '<path d="M10 2h4v3l1 2v13a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2V7l1-2z"/><line x1="9" y1="12" x2="15" y2="12"/>',
    "search": '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
    "send": '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
    "pin": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
}

def ic(name, size=18, color="currentColor", stroke=1.7):
    p = _ICON_PATHS.get(name, "")
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round" style="vertical-align:middle">{p}</svg>')

# ==========================================================================
# Premium styling
# ==========================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    :root {
        --bg:#0a0c10; --bg2:#0d1016; --panel:#12161d; --panel2:#171c24;
        --line:#20262f; --line2:#2a323d;
        --text:#eef1f6; --muted:#7f8896; --faint:#5a6270;
        --accent:#5b8cff; --accent2:#3a6bf0;
        --green:#37d399; --amber:#f5b544; --red:#f56565; --blue:#5b8cff; --violet:#a78bfa;
    }
    html, body, [class*="css"] { font-family:'Inter',system-ui,-apple-system,sans-serif; }
    .stApp { background:
        radial-gradient(1200px 600px at 15% -10%, #12161f 0%, transparent 55%),
        radial-gradient(1000px 500px at 110% 0%, #10141c 0%, transparent 50%),
        var(--bg); color:var(--text); }
    section[data-testid="stSidebar"] { background:var(--bg2); border-right:1px solid var(--line); }
    section[data-testid="stSidebar"] * { color:var(--text); }
    #MainMenu, footer { visibility:hidden; }
    /* Leave Streamlit's header and the native sidebar expand/collapse arrow
       completely alone - hiding the header is what previously broke the
       ability to reopen a collapsed sidebar. */
    .block-container { padding-top:3.4rem; padding-bottom:2.4rem; max-width:1500px; }
    /* remove the Deploy button + top-right toolbar cluster (safe, targeted -
       does NOT touch the sidebar or its expand arrow) */
    [data-testid="stAppDeployButton"], .stDeployButton,
    [data-testid="stToolbarActions"] { display:none !important; }

    h1,h2,h3,h4 { letter-spacing:-0.02em; font-weight:700; }
    .page-title { font-size:1.7rem; font-weight:800; letter-spacing:-0.03em; margin:0; }
    .page-sub { color:var(--muted); font-size:.9rem; margin:.2rem 0 1.3rem; }

    /* cards */
    .pc-card { background:linear-gradient(180deg, var(--panel) 0%, var(--bg2) 100%);
               border:1px solid var(--line); border-radius:16px; padding:18px 20px;
               margin-bottom:16px; box-shadow:0 1px 0 rgba(255,255,255,.02) inset; }

    /* KPI */
    .kpi { position:relative; background:linear-gradient(180deg,var(--panel) 0%,var(--bg2) 100%);
           border:1px solid var(--line); border-radius:16px; padding:18px 20px; height:100%;
           transition:border-color .15s ease, transform .15s ease; overflow:hidden; }
    .kpi:hover { border-color:var(--line2); transform:translateY(-1px); }
    .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
                   background:var(--tint,var(--accent)); opacity:.9; }
    .kpi-top { display:flex; align-items:center; gap:11px; }
    .kpi-ico { width:36px; height:36px; border-radius:10px; display:flex; align-items:center;
               justify-content:center; background:color-mix(in srgb,var(--tint) 14%,transparent);
               color:var(--tint); }
    .kpi-label { color:var(--muted); font-size:.78rem; font-weight:500;
                 text-transform:uppercase; letter-spacing:.04em; }
    .kpi-val { font-size:2rem; font-weight:800; margin:10px 0 2px; letter-spacing:-0.02em; }
    .kpi-sub { color:var(--faint); font-size:.78rem; }

    /* status pill */
    .pill { display:inline-flex; align-items:center; gap:5px; padding:3px 11px; border-radius:999px;
            font-size:.72rem; font-weight:600; letter-spacing:.01em; }
    .pill::before { content:""; width:6px; height:6px; border-radius:50%; background:currentColor; }
    .pill-green { background:rgba(55,211,153,.12); color:#4fe0ab; }
    .pill-amber { background:rgba(245,181,68,.13); color:#f7c368; }
    .pill-red   { background:rgba(245,101,101,.13); color:#f88787; }
    .pill-blue  { background:rgba(91,140,255,.13); color:#84a8ff; }
    .pill-grey  { background:rgba(127,136,150,.13); color:#9aa3b1; }

    /* factor bar */
    .fac { margin-bottom:14px; }
    .fac-row { display:flex; justify-content:space-between; font-size:.84rem; margin-bottom:6px; }
    .fac-track { height:6px; background:#1b212b; border-radius:6px; overflow:hidden; }
    .fac-fill { height:6px; border-radius:6px; }

    /* section label */
    .sect { display:flex; align-items:center; gap:8px; font-size:.95rem; font-weight:700;
            margin:2px 0 12px; color:var(--text); }
    .sect .sect-ic { color:var(--muted); display:inline-flex; }
    .microlabel { color:var(--muted); font-size:.7rem; letter-spacing:.09em;
                  text-transform:uppercase; margin:16px 4px 8px; font-weight:600; }

    /* tables */
    .mtab { width:100%; border-collapse:collapse; font-size:.85rem; }
    .mtab th { text-align:left; color:var(--muted); font-weight:600; padding:9px 10px;
               border-bottom:1px solid var(--line); font-size:.72rem; text-transform:uppercase;
               letter-spacing:.04em; }
    .mtab td { padding:11px 10px; border-bottom:1px solid #171d25; }
    .mtab tr:last-child td { border-bottom:none; }

    /* brand */
    .brand { display:flex; align-items:center; gap:11px; padding:2px 2px 0; }
    .brand-mark { width:38px; height:38px; border-radius:11px; display:flex; align-items:center;
                  justify-content:center; color:#fff;
                  background:linear-gradient(135deg,var(--accent) 0%,var(--violet) 100%);
                  box-shadow:0 6px 18px rgba(91,140,255,.28); }
    .brand-txt { font-size:1.02rem; font-weight:800; line-height:1.1; letter-spacing:-0.02em; }
    .brand-txt small { display:block; color:var(--muted); font-weight:500; font-size:.72rem;
                       letter-spacing:.02em; margin-top:2px; }

    /* nav + sidebar buttons */
    section[data-testid="stSidebar"] .stButton>button {
        background:var(--panel); border:1px solid var(--line); color:var(--muted);
        border-radius:10px; text-align:left; font-weight:500; font-size:.9rem;
        padding:.55rem .75rem; margin-bottom:6px; transition:all .13s ease;
        justify-content:flex-start; }
    section[data-testid="stSidebar"] .stButton>button:hover {
        background:var(--panel2); color:var(--text); border-color:var(--line2); }
    section[data-testid="stSidebar"] .stButton>button[kind="primary"] {
        background:color-mix(in srgb,var(--accent) 16%,transparent);
        color:#cfdcff; border-color:color-mix(in srgb,var(--accent) 45%,transparent);
        font-weight:600; box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 25%,transparent); }

    /* pipeline status rows */
    .pipe { display:flex; align-items:center; gap:10px; padding:9px 11px; border-radius:10px;
            background:var(--panel); border:1px solid var(--line); margin-bottom:8px; }
    .pipe-ic { width:30px; height:30px; border-radius:8px; display:flex; align-items:center;
               justify-content:center; background:color-mix(in srgb,var(--tint) 15%,transparent);
               color:var(--tint); flex:none; }
    .pipe-name { font-size:.82rem; font-weight:600; }
    .pipe-meta { font-size:.72rem; color:var(--muted); }
    .dot-live { width:7px; height:7px; border-radius:50%; background:var(--green);
                box-shadow:0 0 0 3px rgba(55,211,153,.15); margin-left:auto; flex:none; }
    .dot-idle { width:7px; height:7px; border-radius:50%; background:var(--faint); margin-left:auto; flex:none; }

    /* chat bubbles */
    .chat-you { background:linear-gradient(135deg,var(--accent) 0%,var(--accent2) 100%); color:#fff;
                border-radius:14px 14px 3px 14px; padding:10px 13px; font-size:.86rem; margin:5px 0;
                box-shadow:0 4px 12px rgba(58,107,240,.22); }
    .chat-bot { background:var(--panel2); border:1px solid var(--line); border-radius:14px 14px 14px 3px;
                padding:10px 13px; font-size:.86rem; margin:5px 0; line-height:1.5; }
    .chat-meta { color:var(--faint); font-size:.68rem; margin:1px 3px 9px; }

    /* alert card */
    .alrt { display:flex; gap:13px; padding:14px 16px; border-radius:13px; margin-bottom:11px;
            background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--tint); }
    .alrt-ic { color:var(--tint); flex:none; margin-top:1px; }
    .alrt-title { font-weight:650; font-size:.9rem; }
    .alrt-body { color:var(--muted); font-size:.82rem; margin-top:2px; }
    .alrt-time { color:var(--faint); font-size:.7rem; margin-top:5px; }

    .stat-chip { display:inline-flex; align-items:center; gap:6px; padding:5px 11px; border-radius:9px;
                 background:var(--panel); border:1px solid var(--line); font-size:.78rem; margin:0 6px 6px 0; }

    div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:12px; }
    hr { border-color:var(--line); margin:1rem 0; }
</style>
""", unsafe_allow_html=True)

# ==========================================================================
# Session state
# ==========================================================================
st.session_state.setdefault("pipeline_data", None)
st.session_state.setdefault("last_refresh", None)
st.session_state.setdefault("messages", [])
st.session_state.setdefault("page", "Dashboard")
st.session_state.setdefault("scope", "All")
st.session_state.setdefault("ack_alerts", set())
st.session_state.setdefault("th_days_critical", 2)
st.session_state.setdefault("th_value_alert", 5000)


def refresh_pipeline():
    with st.spinner("Running Inventory -> Risk pipeline (DB + live APIs)..."):
        st.session_state.pipeline_data = orchestrator.get_pipeline_data()
        st.session_state.last_refresh = dt.datetime.now().strftime("%H:%M:%S")


def ask(question, history=None, scope="All"):
    sent = question if scope == "All" else f"(Focus only on {scope.lower()} in your answer.) {question}"
    with st.spinner("Thinking... (first Ollama call after startup can take a minute)"):
        out = orchestrator.answer_question(sent, st.session_state.pipeline_data, history=history)
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.messages.append({
        "role": "assistant", "content": out["answer"],
        "direct_lookup": out.get("direct_lookup"), "llm_live": out["llm_live"]})


@st.dialog("Item details")
def show_item_dialog(item_number):
    detail = inventory_agent.item_detail(item_number)
    if detail is None:
        st.error("Item not found."); return
    it, stores = detail["item"], detail["stores"]
    st.markdown(f"### {it['item_description']}")
    st.caption(it["category_name"] or "Uncategorized")
    c1, c2, c3 = st.columns(3)
    c1.metric("Retail price", f"${it['state_bottle_retail']:.2f}")
    c2.metric("Wholesale cost", f"${it['state_bottle_cost']:.2f}")
    c3.metric("Margin/bottle", f"${it['state_bottle_retail'] - it['state_bottle_cost']:.2f}")
    st.markdown("**Supplier**")
    s1, s2, s3, s4 = st.columns(4)
    s1.write(f"Vendor: {it['vendor_name'] or 'Unknown'} (#{it['vendor_number']})")
    s2.write(f"Lead time: {it['lead_time_days']} days" if it["lead_time_days"] else "Lead time: -")
    s3.write(f"Lead time +/-: {it['lead_time_std']:.1f} days" if it["lead_time_std"] else "Lead time +/-: -")
    s4.write(f"Reliability: {it['reliability_score']*100:.0f}%" if it["reliability_score"] else "Reliability: -")
    st.divider()
    st.markdown(f"**Carried at {len(stores)} tracked store(s)**")
    if stores:
        sdf = pd.DataFrame(stores)
        st.dataframe(sdf[["store_number", "store_name", "county", "on_hand_bottles",
                          "reorder_point", "safety_stock", "days_of_cover", "low_stock_flag"]],
                     width='stretch', hide_index=True, column_config={
                         "reorder_point": st.column_config.NumberColumn("Reorder at"),
                         "safety_stock": st.column_config.NumberColumn("Safety stock"),
                         "days_of_cover": st.column_config.NumberColumn("Days cover", format="%.1f"),
                         "low_stock_flag": st.column_config.CheckboxColumn("Low stock")})
    else:
        st.info("No tracked store currently carries this item.")


# ==========================================================================
# Render helpers
# ==========================================================================
def page_head(title, sub):
    st.markdown(f'<div class="page-title">{title}</div>'
                f'<div class="page-sub">{sub}</div>', unsafe_allow_html=True)


def kpi_card(icon, tint, label, value, sub):
    st.markdown(f"""
    <div class="kpi" style="--tint:{tint}">
      <div class="kpi-top">
        <div class="kpi-ico">{ic(icon, 18)}</div>
        <div class="kpi-label">{label}</div>
      </div>
      <div class="kpi-val">{value}</div>
      <div class="kpi-sub">{sub}</div>
    </div>""", unsafe_allow_html=True)


def sect(title, icon=None):
    g = f'<span class="sect-ic">{ic(icon,16)}</span>' if icon else ""
    st.markdown(f'<div class="sect">{g}{title}</div>', unsafe_allow_html=True)


def donut(pairs, center_label, colors):
    real_total = int(sum(v for _, v in pairs))
    df = pd.DataFrame(pairs, columns=["label", "value"])
    df = df[df["value"] > 0]
    if df.empty:
        df = pd.DataFrame([("No data", 1)], columns=["label", "value"])
        colors = {"No data": "#2a3340"}
    order = list(df["label"])
    arc = alt.Chart(df).mark_arc(innerRadius=64, cornerRadius=4).encode(
        theta=alt.Theta("value:Q", stack=True),
        color=alt.Color("label:N", scale=alt.Scale(domain=order,
                        range=[colors.get(l, "#64748b") for l in order]), legend=None),
        tooltip=["label", "value"])
    txt = alt.Chart(pd.DataFrame({"n": [real_total]})).mark_text(
        color="#eef1f6", fontSize=32, fontWeight="bold", dy=-6).encode(text="n:Q")
    sub = alt.Chart(pd.DataFrame({"c": [center_label]})).mark_text(
        color="#7f8896", fontSize=11, dy=17).encode(text="c:N")
    return (arc + txt + sub).properties(height=230, width="container").configure_view(strokeWidth=0)


def legend_rows(pairs, colors, total):
    out = ""
    for label, val in pairs:
        pct = f"{(val/total*100):.0f}%" if total else "0%"
        dot = colors.get(label, "#64748b")
        out += (f'<div style="display:flex;align-items:center;gap:9px;margin:9px 0;font-size:.86rem">'
                f'<span style="width:9px;height:9px;border-radius:50%;background:{dot}"></span>'
                f'<span style="flex:1;color:var(--muted)">{label}</span>'
                f'<span style="font-weight:700">{val}</span>'
                f'<span style="color:var(--faint);width:44px;text-align:right">{pct}</span></div>')
    st.markdown(out, unsafe_allow_html=True)


def factor_bar(name, value, maxval, color, suffix=""):
    pct = int(min(value / maxval, 1) * 100) if maxval else 0
    st.markdown(f"""
    <div class="fac">
      <div class="fac-row"><span style="color:var(--muted)">{name}</span><b>{value}{suffix}</b></div>
      <div class="fac-track"><div class="fac-fill" style="width:{pct}%;background:{color}"></div></div>
    </div>""", unsafe_allow_html=True)


def status_from_risk(r):
    if str(r.get("risk_band", "")).upper() == "HIGH":
        return "At Risk", "pill-red"
    try:
        if r.get("updated_eta") and r.get("promised_eta") and r["updated_eta"] > r["promised_eta"]:
            return "Delayed", "pill-amber"
    except Exception:
        pass
    return "In Transit", "pill-blue"


def data_health_banner(inv, risks, risk_source):
    tracked, at_risk = inv.get("tracked", 0), inv.get("at_risk", 0)
    if tracked == 0:
        st.error("**No inventory data.** `gold_inventory_health` has no rows. Run "
                 "`python databricks_sync.py` (Databricks) or `build_database.py` + "
                 "`simulate.py` (local), then Refresh data.")
        return True
    if len(risks) == 0 and at_risk > 0:
        where = ("`gold_risk_scores` synced empty (likely no HubSpot Deals -> no shipments upstream). "
                 "Re-seed, re-run the Gold notebook, then `databricks_sync.py`."
                 if risk_source == "databricks_gold" else
                 "No `gold_risk_scores` table found and live scoring returned nothing. "
                 "Run `databricks_sync.py` (Databricks) or `simulate.py` (local).")
        st.warning(f"**{at_risk} item(s) flagged, but 0 shipments were scored.** {where} Then Refresh data.")
        return True
    if len(risks) == 0 and at_risk == 0:
        st.info("No low-stock items and no open shipments to score - nothing is currently at risk.")
        return False
    return False


# ==========================================================================
# Alerts engine (wired from live data)
# ==========================================================================
def build_alerts(inv, risks):
    """Derive actionable alerts from the current pipeline output. No backend
    change needed - these are rules over data the agents already produce."""
    alerts = []
    dc = st.session_state.th_days_critical
    vt = st.session_state.th_value_alert

    # stockouts / near-stockouts (Inventory Agent)
    for f in inv.get("flags", []):
        cov = f.get("days_of_cover")
        if cov is None:
            continue
        if cov <= 0:
            alerts.append(("critical", "Stockout",
                f"{f.get('item_description','item')} at store #{f.get('store_number')} is OUT of stock (0 days cover).",
                f"inventory:{f.get('store_number')}:{f.get('item_number')}"))
        elif cov <= dc:
            alerts.append(("high", "Critical low stock",
                f"{f.get('item_description','item')} at store #{f.get('store_number')} has only {cov:.1f} days cover.",
                f"inventory:{f.get('store_number')}:{f.get('item_number')}"))

    # high-risk shipments (Risk Agent)
    for r in risks:
        band = str(r.get("risk_band", "")).upper()
        sid = r.get("shipment_id")
        if band == "HIGH":
            alerts.append(("high", "High delivery risk",
                f"Shipment {sid} to store #{r.get('store_number')} scored {r.get('risk_score')}/100 (HIGH).",
                f"risk:{sid}"))
        elif band == "MEDIUM":
            alerts.append(("warning", "Elevated delivery risk",
                f"Shipment {sid} to store #{r.get('store_number')} scored {r.get('risk_score')}/100 (MEDIUM).",
                f"risk:{sid}"))

    # portfolio value at risk (threshold from Settings)
    var = inv.get("value_at_risk", 0) or 0
    if var >= vt:
        alerts.append(("warning", "Inventory value at risk",
            f"${var:,.0f} of inventory is below the reorder threshold (alert set at ${vt:,.0f}).",
            "portfolio:var"))

    sev_order = {"critical": 0, "high": 1, "warning": 2, "info": 3}
    alerts.sort(key=lambda a: sev_order.get(a[0], 9))
    return alerts


ALERT_STYLE = {"critical": ("#f56565", "alert"), "high": ("#f5b544", "alert"),
               "warning": ("#5b8cff", "bell"), "info": ("#7f8896", "bell")}


# ==========================================================================
# Sidebar
# ==========================================================================
NAV = [("Dashboard", "dashboard"), ("Chat Assistant", "chat"), ("Shipments", "truck"),
       ("Inventory", "box"), ("Risk Monitoring", "shield"), ("CRM / Customers", "users"),
       ("Reports", "report"), ("Alerts", "bell"), ("Settings", "settings")]

with st.sidebar:
    st.markdown(f'<div class="brand"><div class="brand-mark">{ic("activity",20,"#fff")}</div>'
                f'<div class="brand-txt">PourCastAI'
                f'<small>Supply-chain control tower</small></div></div>',
                unsafe_allow_html=True)
    st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)

    # alert count for the nav badge
    _inv0 = (st.session_state.pipeline_data or {}).get("inventory", {}) if st.session_state.pipeline_data else {}
    _risk0 = (st.session_state.pipeline_data or {}).get("risks", []) if st.session_state.pipeline_data else []

    for label, icon in NAV:
        active = st.session_state.page == label
        if st.button(f"{label}", key=f"nav_{label}", width='stretch',
                     type="primary" if active else "secondary"):
            st.session_state.page = label
            st.rerun()

    st.markdown('<div class="microlabel">Agent pipeline</div>', unsafe_allow_html=True)

data = st.session_state.pipeline_data
if data is None:
    refresh_pipeline()
    data = st.session_state.pipeline_data

inv = data["inventory"]
risks = data.get("risks", []) or []
risk_source = data.get("risk_source", "live_scoring")
page = st.session_state.page

# derived
n_ship = len(risks)
bands = pd.Series([str(r.get("risk_band", "")).upper() for r in risks]) if risks else pd.Series([], dtype=str)
n_high = int((bands == "HIGH").sum()); n_med = int((bands == "MEDIUM").sum()); n_low = int((bands == "LOW").sum())
on_time_pct = round((n_low / n_ship) * 100) if n_ship else 0
status_counts = {"In Transit": 0, "Delayed": 0, "At Risk": 0}
for r in risks:
    status_counts[status_from_risk(r)[0]] += 1
alerts = build_alerts(inv, risks)
active_alerts = [a for a in alerts if a[3] not in st.session_state.ack_alerts]

# pipeline status panel (read-only, honest)
with st.sidebar:
    ollama = orchestrator.ollama_status()
    llm_ok = ollama["server_up"] and ollama["model_pulled"]
    pipe = [
        ("box", "#5b8cff", "Inventory Agent", f"{inv['tracked']} tracked · {inv['at_risk']} flagged", True),
        ("shield", "#37d399", "Risk Agent", f"{n_ship} scored · {n_high} high", True),
        ("cpu", "#a78bfa", "LLM Assistant", "Ollama ready" if llm_ok else "Ollama offline", llm_ok),
    ]
    for icon, tint, name, meta, live in pipe:
        dot = "dot-live" if live else "dot-idle"
        st.markdown(f'<div class="pipe" style="--tint:{tint}"><div class="pipe-ic">{ic(icon,16)}</div>'
                    f'<div><div class="pipe-name">{name}</div><div class="pipe-meta">{meta}</div></div>'
                    f'<div class="{dot}"></div></div>', unsafe_allow_html=True)
    st.caption("Fixed pipeline: Inventory -> Risk -> LLM. Not autonomous routing.")

    st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
    if st.button("Refresh data", key="refresh_btn", width='stretch'):
        refresh_pipeline(); st.rerun()
    src_txt = "Databricks Gold" if risk_source == "databricks_gold" else "live scoring"
    st.markdown(f'<div style="color:var(--faint);font-size:.72rem;margin-top:6px">'
                f'Last refresh {st.session_state.last_refresh or "-"} · risk via {src_txt}</div>',
                unsafe_allow_html=True)


# ==========================================================================
# Pages
# ==========================================================================
def render_dashboard():
    page_head("Dashboard", "Overview of your Iowa liquor distribution operations")
    data_health_banner(inv, risks, risk_source)

    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi_card("truck", "#5b8cff", "Tracked shipments", n_ship, "flagged reorders in transit")
    with c2: kpi_card("alert", "#f56565", "At-risk shipments", n_high + n_med, f"{n_high} high · {n_med} medium")
    with c3: kpi_card("check", "#37d399", "On-time rate", f"{on_time_pct}%", "share in LOW risk band")
    with c4: kpi_card("box", "#f5b544", "Inventory alerts", inv["at_risk"], f"of {inv['tracked']} store-items")

    st.write("")
    left, right = st.columns(2)
    with left:
        with st.container(border=False):
            st.markdown('<div class="pc-card">', unsafe_allow_html=True)
            sect("Risk overview", "shield")
            pairs = [("High", n_high), ("Medium", n_med), ("Low", n_low)]
            cols = {"High": "#f56565", "Medium": "#f5b544", "Low": "#37d399"}
            d, l = st.columns([1.2, 1])
            with d: st.altair_chart(donut(pairs, "at risk", cols))
            with l: st.write(""); legend_rows(pairs, cols, n_high + n_med + n_low)
            st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("Shipments by status", "truck")
        pairs = [("In Transit", status_counts["In Transit"]), ("Delayed", status_counts["Delayed"]),
                 ("At Risk", status_counts["At Risk"])]
        cols = {"In Transit": "#5b8cff", "Delayed": "#f5b544", "At Risk": "#f56565"}
        d, l = st.columns([1.2, 1])
        with d: st.altair_chart(donut(pairs, "shipments", cols))
        with l: st.write(""); legend_rows(pairs, cols, n_ship)
        st.markdown('</div>', unsafe_allow_html=True)

    lo, ro = st.columns([1.5, 1])
    with lo:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("Recent shipments", "truck")
        if risks:
            rows = ""
            for r in risks[:6]:
                lbl, cls = status_from_risk(r)
                rows += (f"<tr><td><b>{r.get('shipment_id','-')}</b></td><td>Ankeny DC</td>"
                         f"<td>Store #{r.get('store_number','-')}</td>"
                         f"<td><span class='pill {cls}'>{lbl}</span></td>"
                         f"<td>{r.get('updated_eta') or r.get('promised_eta') or '-'}</td></tr>")
            st.markdown(f'<table class="mtab"><tr><th>Shipment</th><th>Origin</th>'
                        f'<th>Destination</th><th>Status</th><th>ETA</th></tr>{rows}</table>',
                        unsafe_allow_html=True)
        else:
            st.info("No shipments needed re-checking.")
        st.markdown('</div>', unsafe_allow_html=True)
    with ro:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("Top risk factors", "activity")
        wx = sum(1 for r in risks if r.get("weather_alerts"))
        road = sum(1 for r in risks if r.get("road_hazards"))
        rural = sum(1 for r in risks if r.get("rural_delivery"))
        precip = sum(1 for r in risks if (r.get("precip_prob") or 0) >= 0.5)
        mx = max(wx, road, rural, precip, 1)
        factor_bar("Weather alerts (NWS)", wx, mx, "#f56565")
        factor_bar("Road hazards", road, mx, "#f5b544")
        factor_bar("Rural delivery", rural, mx, "#f5b544")
        factor_bar("High precip (>=50%)", precip, mx, "#5b8cff")
        st.markdown('</div>', unsafe_allow_html=True)


def render_shipments():
    """OPERATIONAL view: the shipment list, statuses, ETAs, tracking."""
    page_head("Shipments", "Operational tracking of inbound replenishments from the Ankeny DC")
    if data_health_banner(inv, risks, risk_source):
        return
    it, dl, ar = status_counts["In Transit"], status_counts["Delayed"], status_counts["At Risk"]
    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi_card("truck", "#5b8cff", "In transit", it, "en route to stores")
    with c2: kpi_card("alert", "#f5b544", "Delayed", dl, "ETA slipped")
    with c3: kpi_card("shield", "#f56565", "At risk", ar, "flagged HIGH risk")
    with c4: kpi_card("dollar", "#37d399", "Value in transit",
                      f"${sum((r.get('shipment_value') or 0) for r in risks):,.0f}", "total shipment value")
    st.write("")
    if not risks:
        st.info("No inbound shipments needed re-checking."); return

    st.markdown('<div class="pc-card">', unsafe_allow_html=True)
    sect("Open shipments", "truck")
    ops = []
    for r in risks:
        lbl, _ = status_from_risk(r)
        ops.append({"Shipment": r.get("shipment_id"), "Origin": "Ankeny DC",
                    "Store": f"#{r.get('store_number')}", "Item": r.get("item_description"),
                    "Carrier": r.get("carrier_name"), "Status": lbl,
                    "ETA": r.get("updated_eta") or r.get("promised_eta"),
                    "Value": r.get("shipment_value")})
    odf = pd.DataFrame(ops)
    st.dataframe(odf, width='stretch', hide_index=True, column_config={
        "Value": st.column_config.NumberColumn("Value", format="$%.0f")})
    st.download_button("Export CSV", odf.to_csv(index=False), "shipments.csv", "text/csv")
    st.markdown('</div>', unsafe_allow_html=True)
    st.caption("Status is derived from the risk band + ETA slip (there is no live carrier status feed).")


def render_risk_monitoring():
    """ANALYTICAL view: score distribution, factor drivers, highest-risk drill-down."""
    page_head("Risk Monitoring", "Delivery-risk analytics across all scored shipments (0-100, 7 factors)")
    if data_health_banner(inv, risks, risk_source):
        return
    avg = round(sum((r.get("risk_score") or 0) for r in risks) / n_ship, 1) if n_ship else 0
    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi_card("activity", "#a78bfa", "Mean risk score", avg, "fleet average 0-100")
    with c2: kpi_card("shield", "#f56565", "High risk", n_high, "score >= 60")
    with c3: kpi_card("alert", "#f5b544", "Medium risk", n_med, "score 30-59")
    with c4: kpi_card("check", "#37d399", "Low risk", n_low, "score < 30")
    st.write("")

    left, right = st.columns([1.3, 1])
    with left:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("Score distribution", "activity")
        if risks:
            rdf = pd.DataFrame(risks)
            ch = alt.Chart(rdf).mark_bar(color="#5b8cff", cornerRadius=3).encode(
                x=alt.X("risk_score:Q", bin=alt.Bin(maxbins=12), title="Risk score (0-100)"),
                y=alt.Y("count()", title="Shipments"),
                tooltip=["count()"]).properties(height=250, width="container")
            st.altair_chart(ch)
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("What's driving risk", "shield")
        # prevalence of each factor across the scored fleet (share of shipments affected)
        def share(pred):
            return round(100 * sum(1 for r in risks if pred(r)) / n_ship) if n_ship else 0
        factor_bar("Weather alert at destination", share(lambda r: r.get("weather_alerts")), 100, "#f56565", "%")
        factor_bar("Road hazard on corridor", share(lambda r: r.get("road_hazards")), 100, "#f5b544", "%")
        factor_bar("Rural delivery", share(lambda r: r.get("rural_delivery")), 100, "#a78bfa", "%")
        factor_bar("High precipitation (>=50%)", share(lambda r: (r.get("precip_prob") or 0) >= 0.5), 100, "#5b8cff", "%")
        factor_bar("Long route (>150 km)", share(lambda r: (r.get("distance_km") or 0) > 150), 100, "#37d399", "%")
        st.markdown('</div>', unsafe_allow_html=True)
        st.caption("Share of scored shipments each factor currently affects.")

    st.markdown('<div class="pc-card">', unsafe_allow_html=True)
    sect("Highest-risk shipments", "alert")
    if risks:
        top = sorted(risks, key=lambda r: -(r.get("risk_score") or 0))[:10]
        tdf = pd.DataFrame([{
            "Shipment": r.get("shipment_id"), "Store": f"#{r.get('store_number')}",
            "Item": r.get("item_description"), "Carrier": r.get("carrier_name"),
            "Distance (km)": r.get("distance_km"), "Score": r.get("risk_score"),
            "Band": r.get("risk_band"), "Weather": "Y" if r.get("weather_alerts") else "-",
            "Rural": "Y" if r.get("rural_delivery") else "-"} for r in top])

        def band_color(v):
            return {"HIGH": "background-color:#3a1717;color:#f88787",
                    "MEDIUM": "background-color:#3a2e12;color:#f7c368",
                    "LOW": "background-color:#123326;color:#4fe0ab"}.get(v, "")
        st.dataframe(tdf.style.map(band_color, subset=["Band"]), width='stretch', hide_index=True,
                     column_config={"Score": st.column_config.ProgressColumn(
                         "Score", min_value=0, max_value=100, format="%d")})
    st.markdown('</div>', unsafe_allow_html=True)
    st.caption("Risk = weather (NWS) + road hazard + precip (Open-Meteo) + distance (OSRM) + "
               "diesel (EIA) + carrier reliability + rurality. Weights documented in risk_agent.py.")


def render_inventory():
    page_head("Inventory", "Statistical (s, S) reorder model - Inventory Agent output")
    m1, m2, m3, m4 = st.columns(4)
    with m1: kpi_card("box", "#5b8cff", "Store-items tracked", inv["tracked"], "in the modelled slice")
    with m2: kpi_card("alert", "#f56565", "At risk (<14d)", inv["at_risk"], "below reorder point")
    with m3: kpi_card("dollar", "#37d399", "Inventory value", f"${inv['total_inventory_value']:,.0f}", "total tracked")
    with m4: kpi_card("trenddown", "#f5b544", "Value at risk", f"${inv['value_at_risk']:,.0f}", "in flagged items")
    st.write("")
    left, right = st.columns([1.6, 1])
    with left:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("Flagged store-items", "box")
        if inv["flags"]:
            df = pd.DataFrame(inv["flags"]).rename(columns={
                "state_bottle_retail": "unit_retail", "inventory_value": "value_at_risk_usd"})
            shown = df[["store_number", "store_name", "county", "item_description",
                        "on_hand_bottles", "days_of_cover", "unit_retail", "value_at_risk_usd"]]
            st.dataframe(shown, width='stretch', hide_index=True, column_config={
                "unit_retail": st.column_config.NumberColumn("Unit $", format="$%.2f"),
                "value_at_risk_usd": st.column_config.NumberColumn("Value at risk", format="$%.2f"),
                "days_of_cover": st.column_config.NumberColumn("Days cover", format="%.1f")})
            st.download_button("Export CSV", shown.to_csv(index=False), "inventory_flags.csv", "text/csv")
        else:
            st.success("Nothing under the 14-day threshold.")
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="pc-card">', unsafe_allow_html=True)
        sect("Days of cover", "activity")
        if inv["flags"]:
            df = pd.DataFrame(inv["flags"])
            ch = alt.Chart(df).mark_bar(color="#f5b544", cornerRadius=3).encode(
                x=alt.X("days_of_cover:Q", bin=alt.Bin(maxbins=12), title="Days of cover"),
                y=alt.Y("count()", title="Store-items")).properties(height=230, width="container")
            st.altair_chart(ch)
        st.markdown('</div>', unsafe_allow_html=True)


def render_catalog():
    page_head("Reports", "Browse the tracked product catalogue with supplier and store detail")
    catalog = data.get("catalog", [])
    flagged = {f["item_number"] for f in inv["flags"]}
    f1, f2 = st.columns([3, 1])
    search = f1.text_input("Search", placeholder="Search by name, e.g. vodka, Crown Royal",
                           label_visibility="collapsed")
    cats = sorted({c["category_name"] for c in catalog if c["category_name"]})
    cat_filter = f2.selectbox("Category", ["All categories"] + cats, label_visibility="collapsed")
    rows = catalog
    if search:
        rows = [c for c in rows if search.lower() in c["item_description"].lower()]
    if cat_filter != "All categories":
        rows = [c for c in rows if c["category_name"] == cat_filter]
    st.caption(f"{len(rows)} of {len(catalog)} items")
    if not rows:
        st.info("No items match that filter."); return
    ncols = 4
    for i in range(0, len(rows), ncols):
        cols = st.columns(ncols)
        for c, item in zip(cols, rows[i:i + ncols]):
            with c:
                low = item["item_number"] in flagged
                tag = ('<span class="pill pill-red" style="font-size:.66rem">low</span>' if low
                       else f'<span style="color:var(--faint);font-size:.76rem">{item["total_on_hand"]} on hand</span>')
                st.markdown(f"""<div class="pc-card" style="min-height:158px;margin-bottom:12px">
                    <div style="color:var(--muted)">{ic("bottle",22)}</div>
                    <div style="font-weight:700;font-size:.9rem;margin-top:8px;line-height:1.25">{item['item_description']}</div>
                    <div style="color:var(--muted);font-size:.74rem;margin-top:2px">{item['category_name'] or 'Uncategorized'}</div>
                    <div style="color:var(--amber);font-weight:800;margin-top:8px;font-size:1.05rem">${item['state_bottle_retail']:.2f}</div>
                    <div style="margin-top:4px">{tag}</div></div>""", unsafe_allow_html=True)
                if st.button("View details", key=f"d_{item['item_number']}", width='stretch'):
                    show_item_dialog(item["item_number"])


def render_crm():
    page_head("CRM / Customers", "HubSpot mirror of stores (Companies) and open shipments (Deals)")
    seen, rows = set(), []
    for f in inv["flags"]:
        k = f["store_number"]
        if k in seen: continue
        seen.add(k)
        # shipments to this store
        store_ships = [r for r in risks if r.get("store_number") == k]
        rows.append({"Store #": k, "Store": f.get("store_name"), "County": f.get("county"),
                     "Open shipments": len(store_ships),
                     "At-risk": sum(1 for r in store_ships if str(r.get("risk_band","")).upper() == "HIGH")})
    c1, c2, c3 = st.columns(3)
    with c1: kpi_card("users", "#5b8cff", "Companies (stores)", len(rows) or inv["tracked"], "in HubSpot")
    with c2: kpi_card("truck", "#37d399", "Open deals (shipments)", n_ship, "in the pipeline")
    with c3: kpi_card("shield", "#f56565", "Deals at risk", n_high, "HIGH-risk shipments")
    st.write("")
    st.markdown('<div class="pc-card">', unsafe_allow_html=True)
    sect("Customer stores", "users")
    if rows:
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("No stores in the current flag set.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.caption("Companies = stores; Deals = open shipments across the Ordered / In Transit / "
               "Delivered / At Risk pipeline. Seeded via hubspot_seed.py.")


def render_alerts():
    page_head("Alerts", "Live operational alerts generated from inventory and risk signals")
    top = st.columns([1, 1, 1, 1.4])
    counts = {"critical": 0, "high": 0, "warning": 0}
    for a in active_alerts:
        counts[a[0]] = counts.get(a[0], 0) + 1
    with top[0]: kpi_card("alert", "#f56565", "Critical", counts.get("critical", 0), "immediate action")
    with top[1]: kpi_card("shield", "#f5b544", "High", counts.get("high", 0), "act today")
    with top[2]: kpi_card("bell", "#5b8cff", "Warning", counts.get("warning", 0), "monitor")
    with top[3]:
        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)
        sevs = st.multiselect("Show", ["critical", "high", "warning"],
                              default=["critical", "high", "warning"], label_visibility="collapsed")
    st.write("")

    shown = [a for a in active_alerts if a[0] in sevs]
    if not shown:
        st.success("No active alerts. All clear (or everything is acknowledged).")
    now = dt.datetime.now().strftime("%b %d, %H:%M")
    for sev, title, body, aid in shown:
        color, icon = ALERT_STYLE.get(sev, ALERT_STYLE["info"])
        cc = st.columns([9, 1])
        with cc[0]:
            st.markdown(f'<div class="alrt" style="--tint:{color}"><div class="alrt-ic">{ic(icon,20)}</div>'
                        f'<div style="flex:1"><div class="alrt-title">{title} '
                        f'<span class="pill pill-{"red" if sev=="critical" else "amber" if sev=="high" else "blue"}" '
                        f'style="font-size:.62rem;margin-left:4px">{sev}</span></div>'
                        f'<div class="alrt-body">{body}</div>'
                        f'<div class="alrt-time">Generated {now}</div></div></div>', unsafe_allow_html=True)
        with cc[1]:
            st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
            if st.button("Ack", key=f"ack_{aid}", width='stretch'):
                st.session_state.ack_alerts.add(aid); st.rerun()

    if st.session_state.ack_alerts:
        st.divider()
        if st.button("Restore acknowledged alerts"):
            st.session_state.ack_alerts = set(); st.rerun()
    st.caption("Alerts are rules over live agent output. Thresholds are configurable in Settings. "
               "Acknowledgements last for the session.")


# ---- Settings: read/write .env + live thresholds --------------------------
ENV_PATH = Path(__file__).resolve().parent / ".env"
MANAGED_KEYS = [
    ("OLLAMA_HOST", "Ollama host URL", False),
    ("OLLAMA_MODEL", "Ollama model (restart to apply)", False),
    ("EIA_API_KEY", "EIA API key (live diesel price)", True),
    ("CENSUS_API_KEY", "US Census API key", True),
    ("HUBSPOT_ACCESS_TOKEN", "HubSpot private-app token", True),
    ("DATABRICKS_SERVER_HOSTNAME", "Databricks server hostname", False),
    ("DATABRICKS_HTTP_PATH", "Databricks HTTP path", False),
    ("DATABRICKS_TOKEN", "Databricks token", True),
]

def read_env():
    vals = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals

def write_env(updates):
    existing = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    keys_written, out = set(), []
    for line in existing:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}"); keys_written.add(k); continue
        out.append(line)
    for k, v in updates.items():
        if k not in keys_written:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    for k, v in updates.items():   # apply to the running process where possible
        os.environ[k] = v


def render_settings():
    page_head("Settings", "Manage API credentials, the model, and alert thresholds")
    env = read_env()

    st.markdown('<div class="pc-card">', unsafe_allow_html=True)
    sect("Connections", "activity")
    ollama = orchestrator.ollama_status()
    chips = []
    chips.append(("Ollama", "#37d399" if (ollama["server_up"] and ollama["model_pulled"]) else "#f56565",
                  "ready" if (ollama["server_up"] and ollama["model_pulled"]) else "offline"))
    for key, _, _ in MANAGED_KEYS:
        if key in ("OLLAMA_HOST", "OLLAMA_MODEL"): continue
        has = bool(env.get(key) or os.environ.get(key))
        chips.append((key.replace("_", " ").title(), "#37d399" if has else "#5a6270",
                      "set" if has else "not set"))
    html = ""
    for name, color, state in chips:
        html += (f'<span class="stat-chip"><span style="width:8px;height:8px;border-radius:50%;'
                 f'background:{color}"></span>{name}: <b style="color:{color}">{state}</b></span>')
    st.markdown(html, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="pc-card">', unsafe_allow_html=True)
    sect("API keys & model", "settings")
    reveal = st.toggle("Reveal values", value=False)
    with st.form("env_form"):
        new_vals = {}
        cols = st.columns(2)
        for i, (key, label, secret) in enumerate(MANAGED_KEYS):
            with cols[i % 2]:
                cur = env.get(key, os.environ.get(key, ""))
                new_vals[key] = st.text_input(
                    label, value=cur, key=f"env_{key}",
                    type="default" if (reveal or not secret) else "password")
        saved = st.form_submit_button("Save to .env", type="primary")
    if saved:
        updates = {k: v for k, v in new_vals.items() if v != env.get(k, os.environ.get(k, ""))}
        if updates:
            try:
                write_env(updates)
                st.success(f"Saved {len(updates)} setting(s) to .env. "
                           "Model/host changes take effect after a restart.")
            except Exception as e:
                st.error(f"Could not write .env: {e}")
        else:
            st.info("No changes to save.")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="pc-card">', unsafe_allow_html=True)
    sect("Alert thresholds", "bell")
    st.caption("These update the Alerts page immediately - no restart needed.")
    a, b = st.columns(2)
    with a:
        st.session_state.th_days_critical = st.number_input(
            "Critical low-stock threshold (days of cover)", 0, 30,
            st.session_state.th_days_critical, 1)
    with b:
        st.session_state.th_value_alert = st.number_input(
            "Inventory value-at-risk alert ($)", 0, 1_000_000,
            st.session_state.th_value_alert, 500)
    st.markdown('</div>', unsafe_allow_html=True)
    st.caption("Note: .env is read at process start, so credential changes fully apply on the next "
               "`streamlit run`. Never commit .env - it holds live secrets.")


def render_chat_page():
    page_head("Chat Assistant", "Full-width conversation. The same assistant is docked on every page.")
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.write(m["content"])
            if m["role"] == "assistant":
                src = ("direct database lookup" if m.get("direct_lookup")
                       else "Ollama (live)" if m.get("llm_live") else "templated fallback")
                st.caption(f"Answered via {src}")
    if not st.session_state.messages:
        st.info("No conversation yet - ask below, or use a quick action in the right panel.")
    if q := st.chat_input("e.g. Which stores are at risk of stockout?"):
        ask(q, history=st.session_state.messages, scope=st.session_state.scope); st.rerun()


# ==========================================================================
# Right rail: single AI assistant with a real scope control
# ==========================================================================
def render_assistant_rail():
    st.markdown(f'<div class="sect">{ic("cpu",17)}&nbsp; AI Assistant</div>', unsafe_allow_html=True)
    st.session_state.scope = st.selectbox(
        "Scope", ["All", "Inventory", "Risk"], label_visibility="collapsed",
        index=["All", "Inventory", "Risk"].index(st.session_state.scope),
        help="Narrows what the assistant focuses on. 'All' uses the full pipeline output.")

    with st.container(height=330, border=True):
        if not st.session_state.messages:
            st.caption("Ask a question, or use a quick action below.")
        for m in st.session_state.messages[-8:]:
            if m["role"] == "user":
                st.markdown(f'<div class="chat-you">{m["content"]}</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="chat-bot">{m["content"]}</div>', unsafe_allow_html=True)
                src = ("direct lookup" if m.get("direct_lookup")
                       else "Ollama" if m.get("llm_live") else "fallback")
                st.markdown(f'<div class="chat-meta">via {src}</div>', unsafe_allow_html=True)

    qa = [("Stores at risk of stockout", "Which stores are at risk of stockout?"),
          ("Highest-risk shipment", "What's the highest delivery-risk shipment right now?"),
          ("Inventory value at risk", "How much inventory value is at risk this week?")]
    for label, prompt in qa:
        if st.button(label, key=f"qa_{label}", width='stretch'):
            ask(prompt, history=st.session_state.messages, scope=st.session_state.scope); st.rerun()

    with st.form("rail_chat", clear_on_submit=True):
        txt = st.text_input("msg", placeholder="Type your message...", label_visibility="collapsed")
        sent = st.form_submit_button("Send", width='stretch', type="primary")
    if sent and txt.strip():
        ask(txt.strip(), history=st.session_state.messages, scope=st.session_state.scope); st.rerun()

    model = getattr(orchestrator, "MODEL", "local LLM")
    ext = "live APIs" if data.get("external_data_live") else "fallback data"
    st.markdown(f'<div style="color:var(--faint);font-size:.7rem;margin-top:8px">'
                f'Powered by {model} · {ext}</div>', unsafe_allow_html=True)


# ==========================================================================
# Compose
# ==========================================================================
FULL_WIDTH = {"Chat Assistant", "Reports", "Alerts", "Settings", "Risk Monitoring"}

def render_page():
    if page == "Dashboard": render_dashboard()
    elif page == "Shipments": render_shipments()
    elif page == "Inventory": render_inventory()
    elif page == "Risk Monitoring": render_risk_monitoring()
    elif page == "CRM / Customers": render_crm()
    elif page == "Reports": render_catalog()
    elif page == "Alerts": render_alerts()
    elif page == "Settings": render_settings()
    elif page == "Chat Assistant": render_chat_page()

if page in FULL_WIDTH:
    render_page()
else:
    main, rail = st.columns([2.4, 1], gap="large")
    with main:
        render_page()
    with rail:
        render_assistant_rail()
