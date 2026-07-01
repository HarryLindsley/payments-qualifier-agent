"""End-to-end evaluation run: executes all 8 core synthetic scenarios
through the real orchestrator, checks each result against the
hand-computed ground truth in data/expected_outcomes.json (written
BEFORE this code existed -- see step 5/13 of the build process), runs
the 5-Metric Rule evaluator on each, and writes results.csv +
results_summary.md.

This is the literal evidence behind Report Q7 (Evaluation and Results).
Run this script fresh any time the pipeline changes -- do not hand-edit
the output files.

MOCK LLM CLIENT DISCLOSURE: this run uses a mock Anthropic client for
the one near-miss scenario (SCN-06), not a real API call, because no
API key is available in the environment this was built in. The mock's
JSON-parsing path was separately verified against realistic response
shapes (including markdown-fenced output) in step 9, and the actual
prompt/parsing logic was separately validated against a REAL live API
call via the tot_live_demo.html artifact. This script's SCN-06 result
reflects the mock's fixed scores (see MockClient below), not genuine
model reasoning -- re-run with a real anthropic.Anthropic() client for
a true end-to-end result.
"""
from __future__ import annotations
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qualifier.agents.orchestrator import qualify_payment
from qualifier.logging_observability import TraceLogger, TraceEvaluator, EscalationQueue

DATA_DIR = Path(__file__).parent.parent / "data"
EVAL_DIR = Path(__file__).parent


class MockContent:
    def __init__(self, text): self.text = text


class MockResponse:
    def __init__(self, text): self.content = [MockContent(text)]


class MockClient:
    """See module docstring -- stands in for a real anthropic.Anthropic()
    client. Fixed responses, not live model output."""
    def __init__(self):
        self.messages = self

    def create(self, model, max_tokens, temperature, messages):
        prompt = messages[0]["content"]
        if "Generate exactly" in prompt:
            return MockResponse(
                '[{"hypothesis":"Coincidental name overlap","reasoning":"Common surname root, distinct compound form."},'
                '{"hypothesis":"Same individual, hyphenated surname variant","reasoning":"Given name, middle initial, and root surname match."},'
                '{"hypothesis":"Insufficient evidence to distinguish","reasoning":"Name evidence alone cannot resolve this without a secondary identifier."}]'
            )
        return MockResponse('{"B1": 0.35, "B2": 0.55, "B3": 0.65}')


def load_data():
    return {
        "participants": json.load(open(DATA_DIR / "participants.json")),
        "chunk_store": json.load(open(DATA_DIR / "regulatory_chunks.json")),
        "watchlist": json.load(open(DATA_DIR / "ofac_watchlist.json")),
        "transaction_log": json.load(open(DATA_DIR / "transaction_log.json")),
        "payments": json.load(open(DATA_DIR / "sample_payments.json"))["scenarios"],
        "expected": {e["scenario_id"]: e for e in json.load(open(DATA_DIR / "expected_outcomes.json"))["expected_outcomes"]},
    }


def normalize_verdict(v: str) -> str:
    return "ESCALATED" if v == "ESCALATED_PENDING_DECIDER" else v


def run():
    data = load_data()
    core_scenarios = [s for s in data["payments"] if not s["scenario_id"].startswith("SCN-09")]

    logger = TraceLogger()
    evaluator = TraceEvaluator(logger)
    queue = EscalationQueue()
    mock_client = MockClient()

    rows = []
    for scn in core_scenarios:
        sid = scn["scenario_id"]
        exp = data["expected"][sid]
        result = qualify_payment(
            scn["instruction"], data["participants"], data["chunk_store"],
            data["watchlist"], data["transaction_log"], llm_client=mock_client,
        )
        logger.ingest(result["log"])
        queue.enqueue(result)
        eval_report = evaluator.evaluate(result)

        expected_norm = normalize_verdict(exp["final_verdict"])
        got_norm = normalize_verdict(result["final_verdict"])
        matches_expected = expected_norm == got_norm

        rows.append({
            "scenario_id": sid,
            "test_intent": scn["test_intent"][:80],
            "pillars_run": ",".join(p["pillar"] for p in result["pillar_results"]),
            "final_verdict": result["final_verdict"],
            "expected_verdict": exp["final_verdict"],
            "matches_expected": matches_expected,
            "escalated": result["escalated"],
            "escalation_role": result["escalation_role"] or "",
            "eval_overall": eval_report["overall"],
            "context_adherence": eval_report["metrics"]["context_adherence"]["verdict"],
            "action_advancement": eval_report["metrics"]["action_advancement"]["verdict"],
            "custom_code_eval": eval_report["metrics"]["custom_code_eval"]["verdict"],
            "escalation_routing_accuracy": eval_report["metrics"]["escalation_routing_accuracy"]["verdict"],
            "end_to_end_ms": eval_report["metrics"]["latency"]["end_to_end_ms"],
        })

    # --- results.csv ---
    csv_path = EVAL_DIR / "results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # --- results_summary.md ---
    total = len(rows)
    all_match = sum(r["matches_expected"] for r in rows)
    all_eval_pass = sum(1 for r in rows if r["eval_overall"] == "PASS")
    queue_stats = queue.stats()

    summary_lines = [
        "# Evaluation Results",
        "",
        f"Run against {total} core synthetic scenarios (a 9th, SCN-09, is a reference-only scenario "
        "for the unwired Enrichment Agent concept and is excluded here -- see /reference).",
        "",
        "## Methodology",
        "",
        "Expected outcomes (`data/expected_outcomes.json`) were hand-computed from the synthetic data "
        "BEFORE any pipeline code existed, specifically to avoid grading the system against its own output. "
        "Each row below is the real, compiled LangGraph orchestrator's output, checked against that "
        "pre-existing ground truth.",
        "",
        f"**Ground truth match: {all_match}/{total}** ({'all scenarios matched hand-computed expectations' if all_match==total else 'see mismatches below'})",
        f"**5-Metric evaluator overall PASS: {all_eval_pass}/{total}**",
        "",
        "## Escalation queue",
        "",
        f"- Total escalated: {queue_stats['total_escalated']}/{total}",
        f"- Routed to Validator: {queue_stats['by_role'].get('Validator', 0)}",
        f"- Routed to Decider: {queue_stats['by_role'].get('Decider', 0)}",
        "",
        "**Context note:** this scenario set was deliberately constructed so nearly every scenario "
        "exercises a HOLD/escalation path (one scenario per pillar/branch, by design -- see step 5 of "
        "the build). The high escalation rate here reflects a stress-test scenario mix, not a claim "
        "about real production HOLD rates.",
        "",
        "## Known evaluator limitations, disclosed rather than hidden",
        "",
        "- **Custom Code Eval (output leakage) has never been triggered by real pipeline output** -- "
        "verified correct in isolation against a synthetic leaky string, but no scenario's actual "
        "hold_reason text contains an account number, so this metric has zero real-world trigger "
        "coverage in this MVP.",
        "- **Action Advancement (tool-call loop detection) has never been triggered** -- the "
        "deterministic tools never retry, so this always reports PASS. Correct logic, untested against "
        "a genuine loop.",
        "- **Latency figures are sandbox execution time**, not production network/API latency -- useful "
        "for relative comparison only, not a real SLA measurement.",
        "- **SCN-06's Sanctions ToT branch used a mock LLM client**, not a live API call, in this run "
        "(see script docstring). The prompt/parsing design was separately validated against a real "
        "model via examples/tot_live_demo.html.",
        "",
        "## Per-scenario results",
        "",
        "| Scenario | Final Verdict | Expected | Match | Escalation | 5-Metric | Latency (ms) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        summary_lines.append(
            f"| {r['scenario_id']} | {r['final_verdict']} | {r['expected_verdict']} | "
            f"{'PASS' if r['matches_expected'] else 'MISMATCH'} | {r['escalation_role'] or '-'} | "
            f"{r['eval_overall']} | {r['end_to_end_ms']:.2f} |"
        )

    with open(EVAL_DIR / "results_summary.md", "w") as f:
        f.write("\n".join(summary_lines) + "\n")

    logger.export_jsonl(str(EVAL_DIR / "trace_log.jsonl"))

    print(f"Wrote {csv_path}")
    print(f"Wrote {EVAL_DIR / 'results_summary.md'}")
    print(f"Wrote {EVAL_DIR / 'trace_log.jsonl'}")
    print(f"\nGround truth match: {all_match}/{total}")
    print(f"5-Metric evaluator PASS: {all_eval_pass}/{total}")

    return rows


if __name__ == "__main__":
    run()
