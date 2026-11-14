"""The "ask the model for chart/dashboard JSON, check it, repair it once" flow, written as a LangGraph state machine.

It does exactly what the built-in loop in agent.extract_chart_call does (same prompts, same validation in agent.check_extraction),
so the two can be compared. Switch with Settings -> Tools -> "Chart / dashboard builder" (agent_engine = langgraph).

        +-----+      ok / give up       +-----+
 START->| ask |-->| check |------------>| END |
        +-----+   +-------+  retry (<2) +-----+
           ^          |
           +----------+   (the rejected reply and the reason are added to the conversation)
"""
from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import agent, llm

MAX_ATTEMPTS = 2


class State(TypedDict, total=False):
    msgs: list[dict]
    raw: str
    attempt: int
    obj: Optional[dict]
    problem: str
    stop: bool
    failed: str  # the model could not be reached


def build(ctx, dash: bool):
    async def ask(state: State) -> State:
        try:
            raw = await llm.complete(ctx.s, ctx.model, state["msgs"], max_tokens=1800 if dash else 900, json_mode=True,
                                     num_ctx=6144 if dash else 4096, timeout=900.0 if dash else 400.0)
        except llm.LLMError as exc:
            return {"failed": f"the model did not answer ({str(exc)[:120]})", "stop": True}
        return {"raw": raw or "", "attempt": state.get("attempt", 0) + 1}

    def check(state: State) -> State:
        if state.get("failed"):
            return {}
        obj, problem, give_up = agent.check_extraction(state.get("raw", ""), dash)
        out: State = {"obj": obj, "problem": problem, "stop": bool(obj) or give_up or state.get("attempt", 0) >= MAX_ATTEMPTS}
        if not out["stop"]:  # show the model its own mistake before the next attempt
            out["msgs"] = state["msgs"] + [{"role": "assistant", "content": state.get("raw", "")[:1500]},
                                           {"role": "user", "content": f"That was rejected: {problem}. Fix it and reply with ONLY the corrected JSON object."}]
        return out

    g = StateGraph(State)
    g.add_node("ask", ask)
    g.add_node("check", check)
    g.add_edge(START, "ask")
    g.add_edge("ask", "check")
    g.add_conditional_edges("check", lambda s: "done" if s.get("stop") else "retry", {"done": END, "retry": "ask"})
    return g.compile()


async def extract(ctx, msgs: list[dict], dash: bool, tool: str) -> Optional[dict]:
    final = await build(ctx, dash).ainvoke({"msgs": msgs, "attempt": 0})
    if final.get("obj"):
        return {"id": "call_extracted", "name": tool, "arguments": final["obj"]}
    ctx.extract_error = final.get("failed") or final.get("problem") or "no usable data"
    return None


def diagram() -> str:
    """The graph as Mermaid text (for the README)."""
    class _C:  # a stand-in context: only the graph's shape is needed
        s, model = {}, ""
    return build(_C, True).get_graph().draw_mermaid()
