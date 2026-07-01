"""Data contracts for the Qualifier pipeline. See README Data Contracts
section for the narrative version of these shapes -- this is the
literal, importable definition step 4 committed to.
"""
from __future__ import annotations
from typing import TypedDict, Literal, Optional


class PaymentInstruction(TypedDict):
    instruction_id: str
    amount: float
    currency: str
    debtor_name: str
    debtor_account: str
    debtor_agent_routing: str
    creditor_name: str
    creditor_account: str
    creditor_agent_routing: str
    remittance_information: str
    purpose_code: str
    submitted_at: str


class PillarResult(TypedDict):
    pillar: str
    verdict: str  # "PASS" | "HOLD" | "ESCALATED"
    reason: str
    tool_used: str
    tool_output: dict
    citation: Optional[str]


class QualifierState(TypedDict, total=False):
    instruction: PaymentInstruction
    trace_id: str
    input_gate_passed: bool
    input_gate_issues: list
    reach_result: Optional[PillarResult]
    regulatory_result: Optional[PillarResult]
    sanctions_result: Optional[PillarResult]
    fraud_result: Optional[PillarResult]
    tot_trace: Optional[list]
    final_verdict: Optional[str]  # "PASS" | "HOLD" | "ESCALATED_PENDING_DECIDER" | "BLOCKED_INPUT"
    hold_reason: Optional[str]
    escalated: bool
    escalation_role: Optional[str]  # "Validator" | "Decider"
    log: list


class TraceEvent(TypedDict, total=False):
    trace_id: str
    step_id: str
    timestamp: str
    pillar: str
    action_type: str  # "plan" | "tool_call" | "observe" | "verdict" | "guardrail" | "error"
    thought_summary: str
    tool_name: Optional[str]
    tool_input: Optional[dict]
    tool_output_preview: Optional[str]
    eval_labels: list
    error: Optional[str]


class QualificationResult(TypedDict):
    instruction_id: str
    final_verdict: str
    pillar_results: list
    hold_reason: Optional[str]
    escalated: bool
    escalation_role: Optional[str]
    trace_id: str
