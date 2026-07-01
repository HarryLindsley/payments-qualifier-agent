"""Sanctions Compliance pillar tool: sanctions_screening_tool.

Deterministic fuzzy-match screening against the synthetic OFAC-style
watchlist. This tool itself is NOT an LLM call -- only what happens AFTER
a near-miss score (0.65-0.85) is returned invokes the ToT / LLM reasoning
(see agents/tot_sanctions.py). Confirmed matches (>0.85) and clears
(<0.65) are resolved by this deterministic tool alone.

Similarity method: difflib.SequenceMatcher ratio over lowercased,
stripped name strings, checked against each watchlist entry's primary
name and all aliases; the maximum score across all entries/aliases wins.
This is a simple, interpretable choice appropriate for a first-pass
guardrail (see Lab 6.1 principle: "simple, interpretable methods are
appropriate first-pass guardrails"). Production target would use a
dedicated fuzzy-entity-resolution service, not string similarity alone.
"""
from __future__ import annotations
import difflib
from typing import TypedDict

NEAR_MISS_LOW = 0.65
NEAR_MISS_HIGH = 0.85


class SanctionsScreeningResult(TypedDict):
    match_score: float
    matched_entity: str | None
    matched_entry_id: str | None
    band: str  # "clear" | "near_miss" | "confirmed_match"


def _name_similarity(candidate: str, names: list[str]) -> float:
    candidate_n = candidate.lower().strip()
    best = 0.0
    for n in names:
        ratio = difflib.SequenceMatcher(None, candidate_n, n.lower().strip()).ratio()
        best = max(best, ratio)
    return best


def sanctions_screening_tool(creditor_name: str, watchlist: dict) -> SanctionsScreeningResult:
    best_score = 0.0
    best_entry_name = None
    best_entry_id = None

    for entry in watchlist["watchlist"]:
        names = [entry["primary_name"]] + entry.get("aliases", [])
        score = _name_similarity(creditor_name, names)
        if score > best_score:
            best_score = score
            best_entry_name = entry["primary_name"]
            best_entry_id = entry["entry_id"]

    if best_score < NEAR_MISS_LOW:
        band = "clear"
    elif best_score <= NEAR_MISS_HIGH:
        band = "near_miss"
    else:
        band = "confirmed_match"

    return {
        "match_score": round(best_score, 3),
        "matched_entity": best_entry_name if band != "clear" else None,
        "matched_entry_id": best_entry_id if band != "clear" else None,
        "band": band,
    }
