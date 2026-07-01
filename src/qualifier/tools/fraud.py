"""Fraud Signals pillar tools: duplicate_payment_checker + fraud_pattern_scorer.

Both deterministic -- duplicate detection is an exact-match SQL-style
lookup (here, a linear scan over the synthetic transaction log); fraud
scoring is a fixed weighted-indicator heuristic, not a model call.

duplicate_payment_checker runs FIRST in the Fraud pillar's logic; if a
duplicate is found, fraud_pattern_scorer's result is not needed to reach
a HOLD (though it's still computed and logged for the trace).

fraud_pattern_scorer weighting (all four indicators are independent,
scores are additive, capped at 1.0):
  - round_number_amount   (>= $50,000 and evenly divisible by $50,000): +0.30
  - first_time_beneficiary (creditor_account not seen in transaction log): +0.25
  - anomalous_timing       (submitted before 06:00 or at/after 22:00 UTC): +0.25
  - vague_remittance_reference (< 10 chars, or a generic term like "misc"): +0.20
Threshold for HOLD: score >= 0.65.

NOTE ON FRAGILITY (flagged during step-6 verification): this formula is
tuned against the 8 MVP scenarios and is not a validated production
model. One scenario (SCN-03) independently scores 0.55 on this formula
despite being designed to test the Reach pillar, not Fraud -- it never
reaches this tool because Reach HOLDs first (fail-fast), but changing
that scenario's amount could push it over threshold and create an
unintended double-HOLD. Documented here so it isn't a silent surprise.
"""
from __future__ import annotations
from datetime import datetime
from typing import TypedDict

DUPLICATE_WINDOW_MINUTES_DEFAULT = 10
FRAUD_SCORE_THRESHOLD = 0.65
VAGUE_REMITTANCE_TERMS = {"misc", "payment", "n/a", "other"}


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class DuplicateCheckResult(TypedDict):
    is_duplicate: bool
    matched_instruction_id: str | None
    minutes_since_original: float | None


def duplicate_payment_checker(instruction: dict, transaction_log: dict) -> DuplicateCheckResult:
    window = transaction_log.get("duplicate_window_minutes", DUPLICATE_WINDOW_MINUTES_DEFAULT)
    t = _parse_ts(instruction["submitted_at"])

    for prior in transaction_log["transaction_log"]:
        same_parties_amount = (
            prior["debtor_name"] == instruction["debtor_name"]
            and prior["creditor_name"] == instruction["creditor_name"]
            and prior["amount"] == instruction["amount"]
            and prior["remittance_information"] == instruction["remittance_information"]
        )
        if not same_parties_amount:
            continue
        delta_minutes = abs((t - _parse_ts(prior["submitted_at"])).total_seconds()) / 60
        if delta_minutes <= window:
            return {
                "is_duplicate": True,
                "matched_instruction_id": prior["instruction_id"],
                "minutes_since_original": round(delta_minutes, 2),
            }

    return {"is_duplicate": False, "matched_instruction_id": None, "minutes_since_original": None}


class FraudScoreResult(TypedDict):
    risk_score: float
    indicators: list[str]


def fraud_pattern_scorer(instruction: dict, transaction_log: dict) -> FraudScoreResult:
    score = 0.0
    indicators: list[str] = []

    amount = instruction["amount"]
    if amount >= 50000 and amount % 50000 == 0:
        score += 0.30
        indicators.append("round_number_amount")

    known_accounts = {p["creditor_account"] for p in transaction_log["transaction_log"]}
    if instruction["creditor_account"] not in known_accounts:
        score += 0.25
        indicators.append("first_time_beneficiary")

    hour = _parse_ts(instruction["submitted_at"]).hour
    if hour < 6 or hour >= 22:
        score += 0.25
        indicators.append("anomalous_timing")

    remit = (instruction.get("remittance_information") or "").strip().lower()
    if len(remit) < 10 or remit in VAGUE_REMITTANCE_TERMS:
        score += 0.20
        indicators.append("vague_remittance_reference")

    return {"risk_score": round(min(score, 1.0), 2), "indicators": indicators}


def fraud_pillar_verdict(instruction: dict, transaction_log: dict) -> dict:
    """Combines both fraud tools into the pillar's overall PASS/HOLD verdict."""
    dup = duplicate_payment_checker(instruction, transaction_log)
    score_result = fraud_pattern_scorer(instruction, transaction_log)

    if dup["is_duplicate"]:
        return {
            "verdict": "HOLD", "reason": f"duplicate of {dup['matched_instruction_id']} "
            f"({dup['minutes_since_original']} minutes prior)",
            "duplicate_check": dup, "score_check": score_result,
        }
    if score_result["risk_score"] >= FRAUD_SCORE_THRESHOLD:
        return {
            "verdict": "HOLD", "reason": f"fraud pattern score {score_result['risk_score']} "
            f"exceeds threshold {FRAUD_SCORE_THRESHOLD} ({', '.join(score_result['indicators'])})",
            "duplicate_check": dup, "score_check": score_result,
        }
    return {
        "verdict": "PASS", "reason": "no duplicate detected, fraud score below threshold",
        "duplicate_check": dup, "score_check": score_result,
    }
