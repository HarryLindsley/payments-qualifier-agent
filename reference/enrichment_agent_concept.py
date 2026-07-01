"""REFERENCE / CONCEPT ONLY -- NOT WIRED INTO THE MVP ORCHESTRATOR.

This module lives in /reference, not /src/qualifier, deliberately. It is
a self-contained sketch demonstrating that the Qualifier's escalation
point is a configurable design choice (fully agentic vs. human-in-the-
loop), not a hard architectural limit. It is exercised directly in
isolation (see tests below and data/sample_payments.json SCN-09) but the
orchestrator built for this MVP (src/qualifier/agents/orchestrator.py)
does NOT import or call this module. Every HOLD in the actual MVP
pipeline -- including structural Regulatory violations -- routes to a
human Validator, full stop. This file exists to show how the pattern
COULD extend, not to change what the MVP does.

Enrichment Agent: attempts automated remediation of a HOLD, where safe.

HARD SAFETY BOUNDARY (not configurable, in either mode):
Only structural/format violations from the Regulatory Mandates pillar are
eligible for automated remediation. Reach, Sanctions, and Fraud HOLDs are
NEVER eligible -- these represent eligibility, compliance, or risk
judgment calls, and auto-resolving any of them would violate the
Qualifier's recommendation-only authority principle (see README /
Report Q3) and, for Sanctions specifically, the Module 6 "Mandatory
Near-Miss Escalation" rule that a sanctions determination must never be
auto-resolved. This boundary is enforced in code (REMEDIATION_ELIGIBLE_
PILLARS), not just in documentation, so a mode-flag change alone cannot
widen it.

Currently the only remediation implemented is deterministic truncation of
an over-length remittance_information field. This is intentionally
simple and auditable -- see the IMPLEMENTATION_NOTE below for what a
production version would need instead.
"""
from __future__ import annotations
from typing import Literal

from qualifier.tools.regulatory import rtp_field_validator, REMITTANCE_CHAR_LIMIT

REMEDIATION_ELIGIBLE_PILLARS = {"regulatory"}  # hard boundary -- see module docstring

RemediationMode = Literal["agentic", "human_in_the_loop"]


class RemediationResult(dict):
    """remediated: bool, corrected_instruction: dict|None, note: str"""


def attempt_remediation(instruction: dict, pillar: str, violations: list[str]) -> RemediationResult:
    """Attempt to automatically fix a HOLD. Returns remediated=False for
    anything outside the hard-coded eligible scope, regardless of mode.

    IMPLEMENTATION_NOTE: the current fix (truncate remittance_information
    to the character limit) is a blunt, lossy operation -- it does not
    try to preserve the most important part of the original text (e.g.
    the invoice reference). A production Enrichment Agent would likely
    use an LLM to intelligently shorten the field while preserving key
    references, then re-validate -- deliberately NOT done here to keep
    this MVP's one LLM-using component scoped to the Sanctions ToT branch
    per the locked hybrid design. This is a known, disclosed limitation.
    """
    if pillar not in REMEDIATION_ELIGIBLE_PILLARS:
        return RemediationResult(
            remediated=False, corrected_instruction=None,
            note=f"'{pillar}' pillar HOLDs are not eligible for automated remediation "
                 f"(hard safety boundary -- only structural Regulatory violations are eligible).",
        )

    remittance_violation = any("remittance_information" in v and "exceeds" in v for v in violations)
    if not remittance_violation:
        return RemediationResult(
            remediated=False, corrected_instruction=None,
            note="Regulatory violation is not a remittance-length issue; no automated fix available for this violation type.",
        )

    original = instruction["remittance_information"]
    truncated = original[: REMITTANCE_CHAR_LIMIT - 3].rsplit(" ", 1)[0] + "..."
    corrected = dict(instruction)
    corrected["remittance_information"] = truncated

    revalidation = rtp_field_validator(corrected)
    if revalidation["verdict"] == "PASS":
        return RemediationResult(
            remediated=True, corrected_instruction=corrected,
            note=f"Truncated remittance_information from {len(original)} to {len(truncated)} chars "
                 f"and re-validated successfully. Original text: \"{original}\"",
        )
    return RemediationResult(
        remediated=False, corrected_instruction=None,
        note="Truncation attempt did not resolve all violations; escalating to human Validator.",
    )


def route_hold(instruction: dict, pillar: str, violations: list[str], mode: RemediationMode) -> dict:
    """Top-level routing decision for any HOLD: either escalate straight to
    a human Validator, or (if mode == 'agentic' AND the pillar/violation is
    eligible) attempt the Enrichment Agent first."""
    if mode == "human_in_the_loop" or pillar not in REMEDIATION_ELIGIBLE_PILLARS:
        return {
            "route": "human_validator",
            "reason": "human_in_the_loop mode" if mode == "human_in_the_loop"
                      else f"'{pillar}' pillar is outside the Enrichment Agent's eligible scope",
        }

    result = attempt_remediation(instruction, pillar, violations)
    if result["remediated"]:
        return {"route": "enrichment_agent_resolved", "reason": result["note"], "corrected_instruction": result["corrected_instruction"]}
    return {"route": "human_validator", "reason": result["note"]}
