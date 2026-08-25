"""
agent_graph.py  -  LangGraph wiring for PourCastAI's multi-agent pipeline.

Wraps the EXISTING agents (inventory_agent, risk_agent, orchestrator) as
explicit nodes in a graph, satisfying the "multi-agent architecture" /
"framework beyond raw Python" requirement. This does not change any agent's
business logic - inventory_agent.py and risk_agent.py are untouched - it
only changes how the steps are wired together and makes that wiring visible
and inspectable (e.g. graph.get_graph().draw_mermaid() for the report).

Graph shape:

    START -> guardrail -+-> inventory -> risk -> answer -> END
                         |
                         +-> answer -> END   (out-of-scope question: skip
                                              straight to a canned decline,
                                              never touch inventory/risk/LLM)

NOTE on which one app.py actually uses: this graph re-runs inventory + risk
on EVERY question (the textbook single-shot LangGraph pattern). The live
Streamlit chat (app.py) instead uses orchestrator.get_pipeline_data() /
answer_question(), which caches inventory+risk ONCE per session so follow-up
questions answer fast - see orchestrator.py's module docstring for why. Both
call the exact same underlying inventory_agent/risk_agent/orchestrator
functions - this module is the "reference architecture" for the report and
for standalone graph runs; app.py's caching wrapper is the "chat-optimized"
version for the actual UI. Swapping app.py to call `agent_graph.ask()`
per-question instead is a one-line change if the demo ever needs the graph
running live behind the UI, at the cost of re-fetching inventory/risk (and
therefore hitting live APIs) on every single message.

Run standalone:  python agent_graph.py
"""
from typing import TypedDict, Optional, List, Dict, Any

from langgraph.graph import StateGraph, END

import inventory_agent
import risk_agent
import orchestrator


class PourCastState(TypedDict, total=False):
    question: str
    history: Optional[List[Dict[str, str]]]
    inventory: Optional[dict]
    risks: Optional[list]
    catalog: Optional[list]
    external_data_live: Optional[bool]
    decline: Optional[str]
    answer: Optional[str]
    llm_live: Optional[bool]
    direct_lookup: Optional[bool]


def guardrail_node(state: PourCastState) -> dict:
    """Deterministic out-of-scope check, BEFORE any agent runs. Reuses the
    same guardrail orchestrator.py's chat path uses - see that file's
    _out_of_scope_reply for the actual keyword lists."""
    decline = orchestrator._out_of_scope_reply(state["question"])
    return {"decline": decline}


def inventory_node(state: PourCastState) -> dict:
    """Vishnu-adjacent inventory pull: low-stock flags via the statistical
    reorder-point model in simulate.py, plus the full item catalog."""
    inv = inventory_agent.summary()
    catalog = inventory_agent.item_catalog()
    return {"inventory": inv, "catalog": catalog}


def risk_node(state: PourCastState) -> dict:
    """Sid's Risk agent: scores ONLY the shipments tied to inventory's
    flagged store-items (the deliberate gated pipeline: Risk never runs
    before Inventory has something to check)."""
    risks = risk_agent.run(state["inventory"]["flags"])
    any_live = any(r.get("data_live") for r in risks)
    return {"risks": risks, "external_data_live": any_live}


def answer_node(state: PourCastState) -> dict:
    """Local LLM (or templated fallback) writes the final answer. If the
    guardrail already declined, skip straight to that text - no LLM call."""
    if state.get("decline"):
        return {"answer": state["decline"], "llm_live": False, "direct_lookup": False}
    data = {"inventory": state["inventory"], "risks": state["risks"],
            "catalog": state["catalog"], "external_data_live": state["external_data_live"]}
    out = orchestrator.answer_question(state["question"], data, history=state.get("history"))
    return {"answer": out["answer"], "llm_live": out["llm_live"],
            "direct_lookup": out.get("direct_lookup", False)}


def _route_after_guardrail(state: PourCastState) -> str:
    return "answer" if state.get("decline") else "inventory"


def build_graph():
    g = StateGraph(PourCastState)
    g.add_node("guardrail", guardrail_node)
    g.add_node("inventory", inventory_node)
    g.add_node("risk", risk_node)
    g.add_node("answer", answer_node)

    g.set_entry_point("guardrail")
    g.add_conditional_edges("guardrail", _route_after_guardrail,
                             {"answer": "answer", "inventory": "inventory"})
    g.add_edge("inventory", "risk")
    g.add_edge("risk", "answer")
    g.add_edge("answer", END)
    return g.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def ask(question: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Single entry point: runs the FULL graph for one question (re-fetches
    inventory + risk every call - see module docstring for why app.py's
    live chat uses orchestrator's cached version instead)."""
    graph = get_graph()
    return graph.invoke({"question": question, "history": history or []})


if __name__ == "__main__":
    print("Graph structure:")
    print(get_graph().get_graph().draw_ascii())
    print()
    out = ask("Which stores are at risk of stockout?")
    print(out["answer"])
    print(f"\n[llm_live={out.get('llm_live')} direct_lookup={out.get('direct_lookup')}]")
