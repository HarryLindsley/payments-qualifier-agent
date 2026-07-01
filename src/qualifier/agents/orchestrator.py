"""LangGraph orchestrator for the Payments Qualifier Agent.

Production target design uses CrewAI's Process.hierarchical (per Module 5
capstone architecture) with the Orchestrator as crew manager. This MVP
substitutes LangGraph's StateGraph because no CrewAI implementation
exists anywhere in the curriculum materials to build against safely --
LangGraph has a tested precedent (multi_agent_coordination_and_
communication_solution.ipynb, Part 2) for exactly this shape: shared
state, conditional fail-fast routing, a quality/escalation gate.

Enforces the "thinking state guard" from the Module 2 design: no PASS is
ever emitted without all four pillar observations having actually run.
A HOLD from any pillar short-circuits the remaining pillars (fail-fast)
and routes straight to a human Validator. A Sanctions near-miss
short-circuits to a human Decider via the ToT branch and is NEVER
auto-resolved to PASS or HOLD, per the Module 6 "Mandatory Near-Miss
Escalation" rule.

The Enrichment Agent concept (see /reference) is intentionally NOT
imported or called anywhere in this file -- every HOLD here, including
Regulatory, routes to a human Validator, full stop.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional

from langgraph.graph import StateGraph, START, END

from qualifier.schemas import QualifierState
from qualifier.agents.pillars import run_reach_pillar, run_regulatory_pillar, run_fraud_pillar
from qualifier.tools.sanctions import sanctions_screening_tool
from qualifier.agents.tot_sanctions import run_sanctions_tot
from qualifier.guardrails import check_input_sanitisation, check_confidence_calibration, verify_grounded_citation, check_output_leakage


def _event(trace_id, pillar, action_type, thought_summary):
    return {
        "trace_id": trace_id, "step_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(), "pillar": pillar,
        "action_type": action_type, "thought_summary": thought_summary,
        "tool_name": None, "tool_input": None, "tool_output_preview": None,
        "eval_labels": [], "error": None,
    }


def build_graph(participants: dict, chunk_store: dict, watchlist: dict, transaction_log: dict,
                 llm_client=None, tot_model: str = "claude-sonnet-5"):
    """Builds and compiles the Qualifier's LangGraph. `llm_client` is an
    anthropic.Anthropic() instance (or compatible mock) -- required only
    for the Sanctions near-miss branch. If None, a near-miss case still
    escalates correctly but skips the ToT reasoning step (logged, not
    silently dropped) -- graceful degradation for running without an API
    key, though full fidelity requires a client per the hybrid design.
    """

    def input_gate_node(state: QualifierState) -> QualifierState:
        check = check_input_sanitisation(state["instruction"])
        state["log"].append(_event(
            state["trace_id"], "guardrail", "guardrail",
            f"Gate 1 input sanitisation: {'PASS' if check['proceed'] else 'BLOCKED - ' + '; '.join(check['issues'])}",
        ))
        state["input_gate_passed"] = check["proceed"]
        state["input_gate_issues"] = check["issues"]
        return state

    def reach_node(state: QualifierState) -> QualifierState:
        result, events = run_reach_pillar(state["instruction"], participants, state["trace_id"])
        state["reach_result"] = result
        state["log"].extend(events)
        return state

    def regulatory_node(state: QualifierState) -> QualifierState:
        result, events = run_regulatory_pillar(state["instruction"], chunk_store, state["trace_id"])
        state["regulatory_result"] = result
        state["log"].extend(events)
        return state

    def sanctions_node(state: QualifierState) -> QualifierState:
        inst = state["instruction"]
        state["log"].append(_event(state["trace_id"], "sanctions", "plan",
                                    f"Screening '{inst['creditor_name']}' against watchlist"))
        screening = sanctions_screening_tool(inst["creditor_name"], watchlist)
        state["log"].append(_event(state["trace_id"], "sanctions", "tool_call",
                                    f"sanctions_screening_tool -> score={screening['match_score']} band={screening['band']}"))

        if screening["band"] == "clear":
            result = {
                "pillar": "sanctions", "verdict": "PASS",
                "reason": f"score {screening['match_score']}, no meaningful match",
                "tool_used": "sanctions_screening_tool", "tool_output": screening, "citation": None,
            }
        elif screening["band"] == "confirmed_match":
            result = {
                "pillar": "sanctions", "verdict": "HOLD",
                "reason": f"score {screening['match_score']}, confirmed match to {screening['matched_entity']}",
                "tool_used": "sanctions_screening_tool", "tool_output": screening, "citation": None,
            }
        else:  # near_miss
            if llm_client is not None:
                tot_result = run_sanctions_tot(
                    inst["creditor_name"], screening["matched_entity"], screening["match_score"],
                    llm_client, state["trace_id"], model=tot_model,
                )
                state["tot_trace"] = [
                    {"branch_id": b.branch_id, "hypothesis": b.hypothesis, "reasoning": b.reasoning, "critic_score": b.critic_score}
                    for b in tot_result.branches
                ]
                state["log"].extend(tot_result.trace_events)
                reason = tot_result.escalation_summary
            else:
                state["log"].append(_event(
                    state["trace_id"], "sanctions", "error",
                    "No LLM client provided -- ToT reasoning skipped. Escalating on tool score alone.",
                ))
                reason = (f"score {screening['match_score']}, near-miss vs {screening['matched_entity']} "
                          f"-- ToT skipped (no LLM client), escalating directly to human Decider")
            result = {
                "pillar": "sanctions", "verdict": "ESCALATED",
                "reason": reason, "tool_used": "sanctions_screening_tool",
                "tool_output": screening, "citation": None,
            }

        state["sanctions_result"] = result
        return state

    def fraud_node(state: QualifierState) -> QualifierState:
        result, events = run_fraud_pillar(state["instruction"], transaction_log, state["trace_id"])
        state["fraud_result"] = result
        state["log"].extend(events)
        return state

    def finalize_pass_node(state: QualifierState) -> QualifierState:
        state["final_verdict"] = "PASS"
        state["hold_reason"] = None
        state["escalated"] = False
        state["escalation_role"] = None
        return state

    def finalize_hold_node(state: QualifierState) -> QualifierState:
        for key in ("fraud_result", "sanctions_result", "regulatory_result", "reach_result"):
            r = state.get(key)
            if r and r["verdict"] == "HOLD":
                state["hold_reason"] = r["reason"]
                break
        state["final_verdict"] = "HOLD"
        state["escalated"] = True
        state["escalation_role"] = "Validator"
        return state

    def finalize_escalated_node(state: QualifierState) -> QualifierState:
        state["final_verdict"] = "ESCALATED_PENDING_DECIDER"
        state["hold_reason"] = None
        state["escalated"] = True
        state["escalation_role"] = "Decider"
        return state

    def finalize_blocked_node(state: QualifierState) -> QualifierState:
        state["final_verdict"] = "BLOCKED_INPUT"
        state["hold_reason"] = "; ".join(state.get("input_gate_issues", []))
        state["escalated"] = True
        state["escalation_role"] = "Validator"
        return state

    def guardrail_check_node(state: QualifierState) -> QualifierState:
        """Runs Gate 4 checks (grounded citation, output leakage) plus the
        confidence-calibration check on any ToT recommendation. These are
        audit/redaction guardrails -- they do not change final_verdict
        (that decision is already made), except output leakage, which
        redacts the hold_reason text in place if it leaked an account
        number."""
        pillar_results = [r for r in (
            state.get("reach_result"), state.get("regulatory_result"),
            state.get("sanctions_result"), state.get("fraud_result"),
        ) if r is not None]

        regulatory_result = next((p for p in pillar_results if p["pillar"] == "regulatory"), None)
        if regulatory_result:
            citation_check = verify_grounded_citation(regulatory_result)
            state["log"].append(_event(
                state["trace_id"], "guardrail", "guardrail",
                f"Gate 4 grounded-citation check: {citation_check['verdict']} - {citation_check['note']}",
            ))

        if state.get("tot_trace"):
            combined_text = " ".join(b["reasoning"] for b in state["tot_trace"])
            calibration = check_confidence_calibration(combined_text)
            state["log"].append(_event(
                state["trace_id"], "guardrail", "guardrail",
                f"During-generation confidence calibration on ToT branches: {calibration['verdict']} "
                f"(score {calibration['overconfidence_score']})",
            ))

        reason_text = state.get("hold_reason") or ""
        leakage = check_output_leakage(reason_text)
        state["log"].append(_event(
            state["trace_id"], "guardrail", "guardrail",
            f"Gate 4 output-leakage check: {leakage['verdict']}",
        ))
        if leakage["verdict"] == "BLOCKED_AND_REDACTED":
            state["hold_reason"] = leakage["redacted_text"]

        return state

    def route_after_input_gate(state: QualifierState) -> str:
        return "reach" if state["input_gate_passed"] else "finalize_blocked"

    def route_after_reach(state: QualifierState) -> str:
        return "regulatory" if state["reach_result"]["verdict"] == "PASS" else "finalize_hold"

    def route_after_regulatory(state: QualifierState) -> str:
        return "sanctions" if state["regulatory_result"]["verdict"] == "PASS" else "finalize_hold"

    def route_after_sanctions(state: QualifierState) -> str:
        v = state["sanctions_result"]["verdict"]
        if v == "PASS":
            return "fraud"
        if v == "HOLD":
            return "finalize_hold"
        return "finalize_escalated"

    def route_after_fraud(state: QualifierState) -> str:
        return "finalize_pass" if state["fraud_result"]["verdict"] == "PASS" else "finalize_hold"

    graph = StateGraph(QualifierState)
    graph.add_node("input_gate", input_gate_node)
    graph.add_node("reach", reach_node)
    graph.add_node("regulatory", regulatory_node)
    graph.add_node("sanctions", sanctions_node)
    graph.add_node("fraud", fraud_node)
    graph.add_node("finalize_pass", finalize_pass_node)
    graph.add_node("finalize_hold", finalize_hold_node)
    graph.add_node("finalize_escalated", finalize_escalated_node)
    graph.add_node("finalize_blocked", finalize_blocked_node)
    graph.add_node("guardrail_check", guardrail_check_node)

    graph.add_edge(START, "input_gate")
    graph.add_conditional_edges("input_gate", route_after_input_gate, {"reach": "reach", "finalize_blocked": "finalize_blocked"})
    graph.add_conditional_edges("reach", route_after_reach, {"regulatory": "regulatory", "finalize_hold": "finalize_hold"})
    graph.add_conditional_edges("regulatory", route_after_regulatory, {"sanctions": "sanctions", "finalize_hold": "finalize_hold"})
    graph.add_conditional_edges("sanctions", route_after_sanctions, {
        "fraud": "fraud", "finalize_hold": "finalize_hold", "finalize_escalated": "finalize_escalated",
    })
    graph.add_conditional_edges("fraud", route_after_fraud, {"finalize_pass": "finalize_pass", "finalize_hold": "finalize_hold"})
    graph.add_edge("finalize_pass", "guardrail_check")
    graph.add_edge("finalize_hold", "guardrail_check")
    graph.add_edge("finalize_escalated", "guardrail_check")
    graph.add_edge("finalize_blocked", "guardrail_check")
    graph.add_edge("guardrail_check", END)

    return graph.compile()


def qualify_payment(instruction: dict, participants: dict, chunk_store: dict, watchlist: dict,
                     transaction_log: dict, llm_client=None, tot_model: str = "claude-sonnet-5") -> dict:
    """Convenience entry point: builds the graph fresh and runs one instruction."""
    compiled = build_graph(participants, chunk_store, watchlist, transaction_log, llm_client, tot_model)
    trace_id = uuid.uuid4().hex[:12]
    initial_state: QualifierState = {
        "instruction": instruction, "trace_id": trace_id,
        "input_gate_passed": True, "input_gate_issues": [],
        "reach_result": None, "regulatory_result": None, "sanctions_result": None, "fraud_result": None,
        "tot_trace": None, "final_verdict": None, "hold_reason": None,
        "escalated": False, "escalation_role": None, "log": [],
    }
    final_state = compiled.invoke(initial_state)

    pillar_results = [r for r in (
        final_state.get("reach_result"), final_state.get("regulatory_result"),
        final_state.get("sanctions_result"), final_state.get("fraud_result"),
    ) if r is not None]

    return {
        "instruction_id": instruction["instruction_id"],
        "final_verdict": final_state["final_verdict"],
        "pillar_results": pillar_results,
        "hold_reason": final_state.get("hold_reason"),
        "escalated": final_state["escalated"],
        "escalation_role": final_state.get("escalation_role"),
        "trace_id": trace_id,
        "tot_trace": final_state.get("tot_trace"),
        "log": final_state["log"],
    }
