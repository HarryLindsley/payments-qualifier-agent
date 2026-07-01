# Evaluation Results

Run against 8 core synthetic scenarios (a 9th, SCN-09, is a reference-only scenario for the unwired Enrichment Agent concept and is excluded here -- see /reference).

## Methodology

Expected outcomes (`data/expected_outcomes.json`) were hand-computed from the synthetic data BEFORE any pipeline code existed, specifically to avoid grading the system against its own output. Each row below is the real, compiled LangGraph orchestrator's output, checked against that pre-existing ground truth.

**Ground truth match: 8/8** (all scenarios matched hand-computed expectations)
**5-Metric evaluator overall PASS: 8/8**

## Escalation queue

- Total escalated: 7/8
- Routed to Validator: 6
- Routed to Decider: 1

**Context note:** this scenario set was deliberately constructed so nearly every scenario exercises a HOLD/escalation path (one scenario per pillar/branch, by design -- see step 5 of the build). The high escalation rate here reflects a stress-test scenario mix, not a claim about real production HOLD rates.

## Known evaluator limitations, disclosed rather than hidden

- **Custom Code Eval (output leakage) has never been triggered by real pipeline output** -- verified correct in isolation against a synthetic leaky string, but no scenario's actual hold_reason text contains an account number, so this metric has zero real-world trigger coverage in this MVP.
- **Action Advancement (tool-call loop detection) has never been triggered** -- the deterministic tools never retry, so this always reports PASS. Correct logic, untested against a genuine loop.
- **Latency figures are sandbox execution time**, not production network/API latency -- useful for relative comparison only, not a real SLA measurement.
- **SCN-06's Sanctions ToT branch used a mock LLM client**, not a live API call, in this run (see script docstring). The prompt/parsing design was separately validated against a real model via examples/tot_live_demo.html.

## Per-scenario results

| Scenario | Final Verdict | Expected | Match | Escalation | 5-Metric | Latency (ms) |
|---|---|---|---|---|---|---|
| SCN-01-HAPPY-PATH | PASS | PASS | PASS | - | PASS | 4.34 |
| SCN-02-REACH-INACTIVE | HOLD | HOLD | PASS | Validator | PASS | 1.35 |
| SCN-03-REACH-CAP-BREACH | HOLD | HOLD | PASS | Validator | PASS | 1.37 |
| SCN-04-REGULATORY-FIELD-LENGTH | HOLD | HOLD | PASS | Validator | PASS | 1.76 |
| SCN-05-SANCTIONS-CONFIRMED-MATCH | HOLD | HOLD | PASS | Validator | PASS | 2.85 |
| SCN-06-SANCTIONS-NEAR-MISS-TOT | ESCALATED_PENDING_DECIDER | ESCALATED_PENDING_DECIDER | PASS | Decider | PASS | 2.92 |
| SCN-07-FRAUD-DUPLICATE | HOLD | HOLD | PASS | Validator | PASS | 3.67 |
| SCN-08-FRAUD-BEHAVIORAL-SCORE | HOLD | HOLD | PASS | Validator | PASS | 3.57 |
