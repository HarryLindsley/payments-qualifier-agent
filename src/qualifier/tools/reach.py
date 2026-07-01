"""Reach pillar tool: rtp_participant_lookup.

Deterministic check against the synthetic TCH RTP participant directory.
No LLM call -- this is a fixed-rule lookup, not a judgment call, per the
MVP's hybrid reasoning design (see README Design Decisions).
"""
from __future__ import annotations
from typing import TypedDict


class ReachLookupResult(TypedDict):
    found: bool
    active: bool
    institution_name: str | None
    institution_cap: float | None
    network_cap: float
    verdict: str          # "PASS" or "HOLD"
    reason: str


def rtp_participant_lookup(routing_number: str, amount: float, directory: dict) -> ReachLookupResult:
    """Look up a routing number in the synthetic participant directory and
    determine Reach-pillar PASS/HOLD.

    Args:
        routing_number: creditor_agent_routing from the pacs.008 instruction.
        amount: instructed amount, checked against both the network-wide
            cap and the institution's own (possibly lower) cap.
        directory: parsed contents of data/participants.json.
    """
    network_cap = directory["network_transaction_cap"]
    participants = {p["routing_number"]: p for p in directory["participants"]}

    p = participants.get(routing_number)
    if p is None:
        return {
            "found": False, "active": False, "institution_name": None,
            "institution_cap": None, "network_cap": network_cap,
            "verdict": "HOLD", "reason": f"routing number {routing_number} not found in participant directory",
        }

    if not p["active"]:
        return {
            "found": True, "active": False, "institution_name": p["institution_name"],
            "institution_cap": p["institution_cap"], "network_cap": network_cap,
            "verdict": "HOLD", "reason": f"{p['institution_name']} is not an active RTP participant (destination not RTP-enabled)",
        }

    if amount > network_cap:
        return {
            "found": True, "active": True, "institution_name": p["institution_name"],
            "institution_cap": p["institution_cap"], "network_cap": network_cap,
            "verdict": "HOLD", "reason": f"amount ${amount:,.2f} exceeds network cap ${network_cap:,.2f}",
        }

    if amount > p["institution_cap"]:
        return {
            "found": True, "active": True, "institution_name": p["institution_name"],
            "institution_cap": p["institution_cap"], "network_cap": network_cap,
            "verdict": "HOLD",
            "reason": f"amount ${amount:,.2f} exceeds {p['institution_name']}'s institution cap ${p['institution_cap']:,.2f}",
        }

    return {
        "found": True, "active": True, "institution_name": p["institution_name"],
        "institution_cap": p["institution_cap"], "network_cap": network_cap,
        "verdict": "PASS", "reason": f"{p['institution_name']} is active and ${amount:,.2f} is within cap",
    }
