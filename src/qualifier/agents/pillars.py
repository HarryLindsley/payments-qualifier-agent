"""ReAct-style pillar wrappers for Reach, Regulatory, and Fraud.

Per the MVP's hybrid reasoning decision, these three pillars use
DETERMINISTIC logic wrapped in a ReAct-shaped trace (Thought -> Action ->
Observation), not a live LLM call. The "Thought" step states the fixed
rule being checked rather than open-ended model reasoning -- this keeps
the audit-trail SHAPE consistent with the ReAct pattern from the
curriculum (building_a_react_agent notebook) while being honest that no
model call happens here. Only the Sanctions near-miss branch
(tot_sanctions.py) makes a real LLM call, per the locked hybrid design.

Each wrapper returns a list of TraceEvent-shaped dicts (see
schemas.py / README Data Contracts) plus the pillar's PillarResult.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone

from qualifier.tools.reach import rtp_participant_lookup
from qualifier.tools.regulatory import rtp_field_validator, regulatory_rule_retriever
from qualifier.tools.fraud import fraud_pillar_verdict


def _trace_event(trace_id, pillar, action_type, thought_summary, tool_name=None,
                  tool_input=None, tool_output_preview=None):
    return {
        "trace_id": trace_id,
        "step_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pillar": pillar,
        "action_type": action_type,
        "thought_summary": thought_summary,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output_preview": tool_output_preview,
        "eval_labels": [],
        "error": None,
    }


def run_reach_pillar(instruction: dict, participants_directory: dict, trace_id: str) -> tuple[dict, list[dict]]:
    """Deterministic Reach check, wrapped in a ReAct-shaped trace."""
    events = []
    events.append(_trace_event(
        trace_id, "reach", "plan",
        f"Check whether routing {instruction['creditor_agent_routing']} is an active RTP "
        f"participant and whether ${instruction['amount']:,.2f} is within its cap.",
    ))

    result = rtp_participant_lookup(
        instruction["creditor_agent_routing"], instruction["amount"], participants_directory
    )
    events.append(_trace_event(
        trace_id, "reach", "tool_call", "Called rtp_participant_lookup",
        tool_name="rtp_participant_lookup",
        tool_input={"routing_number": instruction["creditor_agent_routing"], "amount": instruction["amount"]},
        tool_output_preview=str(result)[:200],
    ))
    events.append(_trace_event(trace_id, "reach", "verdict", result["reason"]))

    pillar_result = {
        "pillar": "reach", "verdict": result["verdict"], "reason": result["reason"],
        "tool_used": "rtp_participant_lookup", "tool_output": result, "citation": None,
    }
    return pillar_result, events


def run_regulatory_pillar(instruction: dict, chunk_store: dict, trace_id: str) -> tuple[dict, list[dict]]:
    """Deterministic Regulatory check (field validation + grounded rule retrieval),
    wrapped in a ReAct-shaped trace. Verdict must carry a citation per the
    Grounded-Citation Requirement guardrail (Module 6)."""
    events = []
    events.append(_trace_event(
        trace_id, "regulatory", "plan",
        "Validate pacs.008 field structure, then retrieve the governing remittance-length "
        "rule as of the instruction's submission date.",
    ))

    field_result = rtp_field_validator(instruction)
    events.append(_trace_event(
        trace_id, "regulatory", "tool_call", "Called rtp_field_validator",
        tool_name="rtp_field_validator", tool_input={"instruction_id": instruction["instruction_id"]},
        tool_output_preview=str(field_result)[:200],
    ))

    as_of_date = instruction["submitted_at"][:10]
    chunks = regulatory_rule_retriever(["remittance", "140 characters"], as_of_date, chunk_store)
    citation = None
    if chunks:
        c = chunks[0]
        citation = f"{c['chunk_id']} ({c['source_doc']}, effective {c['effective_date']})"
        events.append(_trace_event(
            trace_id, "regulatory", "tool_call", "Retrieved governing rule chunk",
            tool_name="regulatory_rule_retriever",
            tool_input={"topic_keywords": ["remittance", "140 characters"], "as_of_date": as_of_date},
            tool_output_preview=citation,
        ))

    if field_result["verdict"] == "PASS":
        reason = "all required fields present, remittance within 140-char limit"
    else:
        reason = "; ".join(field_result["violations"])
    events.append(_trace_event(trace_id, "regulatory", "verdict", reason))

    pillar_result = {
        "pillar": "regulatory", "verdict": field_result["verdict"], "reason": reason,
        "tool_used": "rtp_field_validator", "tool_output": field_result, "citation": citation,
    }
    return pillar_result, events


def run_fraud_pillar(instruction: dict, transaction_log: dict, trace_id: str) -> tuple[dict, list[dict]]:
    """Deterministic Fraud check (duplicate + behavioral scoring), wrapped in a
    ReAct-shaped trace."""
    events = []
    events.append(_trace_event(
        trace_id, "fraud", "plan",
        "Check for a duplicate submission within the window, then score behavioral "
        "fraud indicators if no duplicate is found.",
    ))

    result = fraud_pillar_verdict(instruction, transaction_log)
    events.append(_trace_event(
        trace_id, "fraud", "tool_call", "Called duplicate_payment_checker + fraud_pattern_scorer",
        tool_name="fraud_pillar_verdict",
        tool_input={"instruction_id": instruction["instruction_id"]},
        tool_output_preview=str(result)[:200],
    ))
    events.append(_trace_event(trace_id, "fraud", "verdict", result["reason"]))

    pillar_result = {
        "pillar": "fraud", "verdict": result["verdict"], "reason": result["reason"],
        "tool_used": "fraud_pillar_verdict", "tool_output": result, "citation": None,
    }
    return pillar_result, events
