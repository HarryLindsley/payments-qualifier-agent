# Pillar Logic Reference

This is the single source of truth for what each pillar actually checks, the exact thresholds/weights involved, and why. Code docstrings in `src/qualifier/tools/` are the literal implementation; this document is the human-readable explanation that sits above them. If this document and the code ever disagree, the code is correct and this document is stale -- open an issue.

Every pillar runs in a fixed order: **Reach -> Regulatory -> Sanctions -> Fraud**, fail-fast (a HOLD at any pillar stops the rest). This order is inherited from the original Module 2/5 design, not something re-derived here, but the reasoning holds up: cheapest and most structural checks run first (can this payment even reach its destination, is it well-formed), before spending effort on fuzzy matching (Sanctions) or multi-signal scoring (Fraud). Sanctions runs before Fraud because a sanctions match is a hard compliance gate -- if the beneficiary can't legally be paid, no fraud analysis is relevant.

---

## 1. Reach

**Question it answers:** Can this payment even get to its destination?

**Logic** (`tools/reach.py`, `rtp_participant_lookup`):
1. Look up `creditor_agent_routing` in the participant directory.
2. Not found → HOLD ("routing number not found").
3. Found but `active: false` → HOLD ("not an active RTP participant"). This is the case where an institution has exited the network.
4. Amount exceeds the network-wide cap ($10,000,000, verified) → HOLD.
5. Amount exceeds the *institution's own* cap (which may be lower than the network cap -- e.g. a smaller participant electing a conservative $2M limit) → HOLD, with a distinct reason from a network-cap breach.
6. Otherwise → PASS.

**Why two separate cap checks:** the network cap and an institution's own cap are genuinely different rules with different sources -- the network cap is a TCH-wide ceiling (verified, $10M), while an institution-specific cap is that bank's own risk appetite. Conflating them into one check would lose the distinction between "this violates network policy" and "this specific bank doesn't want transfers this large," which matters for how a Validator would investigate the HOLD.

**What's illustrative vs. verified:** the $10M network cap is verified. Which specific institutions are active, and what their individual caps are, is entirely synthetic (see `data/participants.json`).

---

## 2. Regulatory Mandates

**Question it answers:** Is this instruction structurally valid, and does the underlying rule actually say what we claim it says?

**Logic** (`tools/regulatory.py`, `rtp_field_validator` + `regulatory_rule_retriever`):
1. Check all required pacs.008 fields are present.
2. Check `remittance_information` is ≤ 140 characters (verified limit; see the disclosed simplification below).
3. Retrieve the governing rule chunk from the synthetic RAG store, filtered so any chunk superseded before the instruction's date is excluded (this is the effective-date filtering mechanism).
4. Any violation → HOLD, and the verdict must carry the retrieved chunk's citation (chunk ID + effective date) — this is the Grounded-Citation Requirement guardrail (Gate 4), checked independently after the verdict is formed.
5. Otherwise → PASS.

**Why a citation is mandatory for a violation but not for a clean PASS:** a HOLD is an assertion ("this violates rule X") that needs to be traceable to a source. A PASS with no violations found isn't asserting anything about rule text -- there's nothing to ground.

**Disclosed simplification:** the real ISO 20022 spec actually permits up to 3 x 140 = 420 characters across repeated instances. This system deliberately enforces the *stricter* single-instance, 140-char rule, matching the original Module 5 design. An instruction between 141-420 characters would be correctly accepted by the real network but gets flagged HOLD here. This is a scope choice, not an error in the underlying 140-char fact.

---

## 3. Sanctions Compliance

**Question it answers:** Is the beneficiary someone we are legally barred from paying?

**Logic** (`tools/sanctions.py`, `sanctions_screening_tool` + `agents/tot_sanctions.py`):
1. Compute a similarity score (0.0-1.0) between the beneficiary name and every watchlist entry's primary name and aliases, using `difflib.SequenceMatcher`. Take the maximum score across all entries.
2. **Score < 0.65 → clear, PASS.** No further action.
3. **Score > 0.85 → confirmed match, HOLD.** Automatic -- no Tree-of-Thought reasoning needed, because the match is unambiguous enough that human judgment isn't adding information. Escalates to a **Validator**.
4. **Score 0.65-0.85 → near-miss.** This is the one genuinely ambiguous case in the whole system. Triggers Tree-of-Thought reasoning (Generator produces 3 competing hypotheses about what the match represents, Critic scores each) and *always* escalates to a **Decider** -- the ToT's output is a recommendation, never an auto-resolution, regardless of how confident the model sounds.

**Why 0.65 and 0.85 specifically:** these are illustrative, chosen so the engineered test data lands cleanly in each band -- not derived from any real OFAC or TCH sanctions-screening methodology. Flagged clearly in `data/ofac_watchlist.json` and in the code.

**Why string similarity, not something more sophisticated:** `difflib.SequenceMatcher` is simple, dependency-free, and auditable -- appropriate for a first-pass demonstration guardrail (same principle Lab 6.1 uses). It will not catch phonetic variants, transliterations, or reordered names. A production system would use a dedicated fuzzy-entity-resolution service.

---

## 4. Fraud Signals

**Question it answers:** Does this transaction's *pattern* look suspicious, independent of who the beneficiary is?

This pillar has two independent checks, run in sequence (`tools/fraud.py`):

**Check 1 — Duplicate detection** (`duplicate_payment_checker`): exact match on debtor name + creditor name + amount + remittance reference, against the transaction log, within a 10-minute window. Match found → HOLD immediately. The behavioral score below is still computed and logged, but doesn't change the verdict -- a duplicate is a duplicate regardless of how "normal" the transaction otherwise looks.

**Check 2 — Behavioral scoring** (`fraud_pattern_scorer`), only reached if it's not a duplicate. Four independent indicators, additive, capped at 1.0:

| Indicator | Condition | Weight |
|---|---|---|
| Round-number amount | Amount ≥ $50,000 and evenly divisible by $50,000 | +0.30 |
| First-time beneficiary | Creditor account never seen in the transaction log | +0.25 |
| Anomalous timing | Submitted before 06:00 or at/after 22:00 UTC | +0.25 |
| Vague remittance reference | Remittance text under 10 characters, or a generic term ("misc", "payment", "n/a", "other") | +0.20 |

**Threshold: score ≥ 0.65 → HOLD.** Escalates to a **Validator**, not a Decider -- this is a heuristic pattern match against defined criteria, not the kind of irreducible identity ambiguity a Sanctions near-miss represents.

**Why these four indicators and these weights:** they are mine, chosen only so the two fraud test scenarios (SCN-07 duplicate, SCN-08 behavioral) land where designed. Not derived from any historical fraud dataset or validated model. Disclosed explicitly in code and in the capstone report (Q8/Q9) as illustrative and untuned. A real deployment would calibrate these against actual fraud-labeled transaction history before they could inform any real decision.

**A known fragility, disclosed rather than hidden:** SCN-03 (a Reach-pillar test scenario, unrelated to fraud) independently scores 0.55 on this formula -- close to the 0.65 threshold, though it never reaches the Fraud pillar since Reach HOLDs first. Changing that scenario's amount could accidentally push it over threshold and create an unintended double-HOLD. Not a bug, just a coupling worth knowing about if the synthetic data changes.

---

## Escalation summary across all four pillars

| Pillar | HOLD trigger | Escalates to | Why that role |
|---|---|---|---|
| Reach | Inactive participant, or amount exceeds network/institution cap | Validator | Deterministic finding, needs confirmation/correction |
| Regulatory | Field violation (with mandatory citation) | Validator | Deterministic finding, needs confirmation/correction |
| Sanctions (confirmed) | Score > 0.85 | Validator | Unambiguous match, needs confirmation/correction |
| Sanctions (near-miss) | Score 0.65-0.85 | **Decider** | Genuine ambiguity the system cannot resolve alone |
| Fraud | Duplicate, or behavioral score ≥ 0.65 | Validator | Heuristic pattern match, needs confirmation/correction |
