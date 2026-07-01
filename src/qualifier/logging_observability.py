"""Hand-rolled observability layer, standing in for LangSmith per the
locked hybrid design (see README Design Decisions -- LangSmith is the
production target, substituted here to avoid an external account
dependency in a repo meant to run with zero setup in Colab).

Adapted from Lab 6.2 (logging_observability_and_human_intervention_in_
agent_systems_solution.ipynb)'s TraceLogger / TraceEvaluator /
EscalationQueue pattern, with TraceEvaluator's metrics replaced entirely
by the exact 5 metrics specified in your Module 6 capstone doc's
"Evaluation Metrics" table (the 5-Metric Rule), not the generic metrics
from the lab.

NOTE ON THE "Editor" DISCREPANCY: the Module 6 capstone doc's evaluation
metrics table says a Context Adherence failure routes to an "Editor"
role, but the same document's Human Intervention Criteria table only
defines Decider, Validator, and Teacher -- "Editor" does not appear
anywhere else. Treated here as a likely naming slip and routed to
Validator instead (closest existing role: "reviews before correction/
resubmission"). Flagged, not silently resolved -- worth a source-doc
correction if this was intentional.
"""
from __future__ import annotations
import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

_ACCOUNT_PATTERN = re.compile(r"SYN-ACCT-\d{4,}")


# ---------------------------------------------------------------------------
# TraceLogger
# ---------------------------------------------------------------------------

class TraceLogger:
    """Collects TraceEvents across one or more qualification runs, indexed
    by trace_id. In production this would stream to LangSmith; here it's
    an in-memory store with JSONL export."""

    def __init__(self):
        self._traces: dict[str, list[dict]] = defaultdict(list)

    def ingest(self, log: list[dict]) -> None:
        """Ingest the `log` list returned by qualify_payment()."""
        for event in log:
            self._traces[event["trace_id"]].append(event)

    def get_trace(self, trace_id: str) -> list[dict]:
        return sorted(self._traces.get(trace_id, []), key=lambda e: e["timestamp"])

    def all_trace_ids(self) -> list[str]:
        return list(self._traces.keys())

    def export_jsonl(self, path: str) -> int:
        count = 0
        with open(path, "w") as f:
            for trace_id in self._traces:
                for event in self.get_trace(trace_id):
                    f.write(json.dumps(event) + "\n")
                    count += 1
        return count


# ---------------------------------------------------------------------------
# TraceEvaluator -- the 5-Metric Rule, exactly as specified in the Module 6
# capstone doc's Evaluation Metrics table
# ---------------------------------------------------------------------------

class TraceEvaluator:
    def __init__(self, trace_logger: TraceLogger):
        self.logger = trace_logger

    def context_adherence(self, pillar_results: list[dict]) -> dict:
        """Regulatory pillar output is strictly grounded in retrieved scheme
        text, not fabricated. Below threshold -> block output, route to
        Validator (see module docstring re: 'Editor' discrepancy)."""
        regulatory = next((p for p in pillar_results if p["pillar"] == "regulatory"), None)
        if regulatory is None:
            return {"metric": "context_adherence", "verdict": "N/A", "detail": "Regulatory pillar did not run (fail-fast)"}

        asserted_violation = bool(regulatory["tool_output"].get("violations"))
        if not asserted_violation:
            return {"metric": "context_adherence", "verdict": "PASS", "detail": "no rule-based claim asserted"}

        if regulatory.get("citation"):
            return {"metric": "context_adherence", "verdict": "PASS", "detail": f"grounded: {regulatory['citation']}"}

        return {
            "metric": "context_adherence", "verdict": "FAIL",
            "detail": "violation asserted without citation -- ungrounded",
            "action": "block output, route to Validator",
        }

    def action_advancement(self, trace_id: str) -> dict:
        """Detects tool-call loops (e.g. repeated identical tool calls within
        one trace). Loop detected -> halt and escalate."""
        events = self.logger.get_trace(trace_id)
        tool_calls = [(e["tool_name"], json.dumps(e.get("tool_input"), sort_keys=True))
                      for e in events if e["action_type"] == "tool_call" and e.get("tool_name")]
        seen = set()
        loops = []
        for call in tool_calls:
            if call in seen:
                loops.append(call)
            seen.add(call)

        if loops:
            return {"metric": "action_advancement", "verdict": "FAIL", "detail": f"repeated tool call(s): {loops}", "action": "halt and escalate"}
        return {"metric": "action_advancement", "verdict": "PASS", "detail": f"{len(tool_calls)} distinct tool call(s), no loop"}

    def custom_code_eval(self, result_text: str) -> dict:
        """Deterministic scan for PII / raw account numbers in output. Any
        hit -> redact and block. (Same mechanism as guardrails.
        check_output_leakage -- duplicated here deliberately, since the
        evaluator is meant to independently re-check output quality after
        the fact, not simply trust that the guardrail already ran.)"""
        matches = _ACCOUNT_PATTERN.findall(result_text or "")
        if matches:
            return {"metric": "custom_code_eval", "verdict": "FAIL", "detail": f"leaked account(s): {matches}", "action": "redact and block"}
        return {"metric": "custom_code_eval", "verdict": "PASS", "detail": "no PII/account numbers detected in output"}

    def escalation_routing_accuracy(self, sanctions_result: Optional[dict], final_verdict: str, escalation_role: Optional[str]) -> dict:
        """Did near-miss and high-risk cases correctly reach a human Decider,
        rather than auto-passing? Miss -> recalibrate scorer threshold."""
        if sanctions_result is None:
            return {"metric": "escalation_routing_accuracy", "verdict": "N/A", "detail": "Sanctions pillar did not run (fail-fast)"}

        band = sanctions_result["tool_output"].get("band")
        if band != "near_miss":
            return {"metric": "escalation_routing_accuracy", "verdict": "PASS", "detail": f"band={band}, no escalation required"}

        correctly_escalated = (final_verdict == "ESCALATED_PENDING_DECIDER" and escalation_role == "Decider")
        if correctly_escalated:
            return {"metric": "escalation_routing_accuracy", "verdict": "PASS", "detail": "near-miss correctly routed to Decider, not auto-resolved"}
        return {
            "metric": "escalation_routing_accuracy", "verdict": "FAIL",
            "detail": f"near-miss band but final_verdict={final_verdict}, escalation_role={escalation_role} -- should never auto-resolve",
            "action": "recalibrate scorer threshold",
        }

    def latency(self, trace_id: str) -> dict:
        """Time from instruction receipt to recommendation, end-to-end and
        per-pillar. SLA breach -> move heavy judges to async, sampled
        review. NOTE: these are sandbox execution timestamps, not
        production network/API latency -- useful for relative comparison
        (e.g. which pillar is slowest) but not a real SLA measurement."""
        events = self.logger.get_trace(trace_id)
        if not events:
            return {"metric": "latency", "verdict": "N/A", "detail": "no events for trace"}

        timestamps = [datetime.fromisoformat(e["timestamp"]) for e in events]
        end_to_end_ms = (max(timestamps) - min(timestamps)).total_seconds() * 1000

        per_pillar = defaultdict(list)
        for e in events:
            per_pillar[e["pillar"]].append(datetime.fromisoformat(e["timestamp"]))
        per_pillar_ms = {
            p: round((max(ts) - min(ts)).total_seconds() * 1000, 2)
            for p, ts in per_pillar.items() if len(ts) > 1
        }

        return {
            "metric": "latency", "verdict": "INFO",
            "end_to_end_ms": round(end_to_end_ms, 2), "per_pillar_ms": per_pillar_ms,
            "detail": "sandbox execution timing, not a production SLA measurement",
        }

    def evaluate(self, qualification_result: dict) -> dict:
        """Runs all 5 metrics against one completed qualify_payment() result."""
        pillar_results = qualification_result["pillar_results"]
        sanctions_result = next((p for p in pillar_results if p["pillar"] == "sanctions"), None)
        result_text = qualification_result.get("hold_reason") or ""

        metrics = {
            "context_adherence": self.context_adherence(pillar_results),
            "action_advancement": self.action_advancement(qualification_result["trace_id"]),
            "custom_code_eval": self.custom_code_eval(result_text),
            "escalation_routing_accuracy": self.escalation_routing_accuracy(
                sanctions_result, qualification_result["final_verdict"], qualification_result.get("escalation_role"),
            ),
            "latency": self.latency(qualification_result["trace_id"]),
        }

        fails = [m for m in metrics.values() if m.get("verdict") == "FAIL"]
        return {
            "trace_id": qualification_result["trace_id"],
            "instruction_id": qualification_result["instruction_id"],
            "metrics": metrics,
            "overall": "FAIL" if fails else "PASS",
            "fail_count": len(fails),
        }


# ---------------------------------------------------------------------------
# EscalationQueue
# ---------------------------------------------------------------------------

class EscalationQueue:
    """Queues qualification results that require human action, triaged by
    role per the Module 6 Human Intervention Criteria table."""

    def __init__(self):
        self._queue: list[dict] = []

    def enqueue(self, qualification_result: dict) -> Optional[dict]:
        if not qualification_result.get("escalated"):
            return None
        item = {
            "instruction_id": qualification_result["instruction_id"],
            "trace_id": qualification_result["trace_id"],
            "role": qualification_result.get("escalation_role"),
            "final_verdict": qualification_result["final_verdict"],
            "reason": qualification_result.get("hold_reason") or
                      (qualification_result.get("tot_trace") and "sanctions near-miss, see tot_trace") or
                      "unspecified",
            "resolved": False,
            "resolution": None,
        }
        self._queue.append(item)
        return item

    def pending(self, role: Optional[str] = None) -> list[dict]:
        items = [i for i in self._queue if not i["resolved"]]
        if role:
            items = [i for i in items if i["role"] == role]
        return items

    def resolve(self, instruction_id: str, resolution: str) -> bool:
        for item in self._queue:
            if item["instruction_id"] == instruction_id and not item["resolved"]:
                item["resolved"] = True
                item["resolution"] = resolution
                return True
        return False

    def stats(self) -> dict:
        by_role = defaultdict(int)
        for i in self._queue:
            by_role[i["role"]] += 1
        return {
            "total_escalated": len(self._queue),
            "pending": len(self.pending()),
            "resolved": len([i for i in self._queue if i["resolved"]]),
            "by_role": dict(by_role),
        }
