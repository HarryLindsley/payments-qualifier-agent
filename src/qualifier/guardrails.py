"""Guardrails, adapted from Lab 6.1 (guardrailing_hallucinations_and_
overconfidence_in_agent_outputs_solution.ipynb) and mapped onto the four
Module 6 gates for this domain:

  Lab 6.1 stage          -> This module's function       -> Module 6 gate
  pre-generation           check_input_sanitisation         Gate 1 (Before Input)
  during-generation         check_confidence_calibration     (ToT recommendation tone)
  post-generation           verify_grounded_citation          Gate 4 (Source Verification)
  (new, payments-specific)  check_output_leakage              Gate 4 (Output Constraint)

Two design notes carried over from Lab 6.1, disclosed rather than fixed:
  - check_confidence_calibration reuses the same hand-crafted regex
    marker list as the lab; it is brittle by nature (misses novel
    phrasing) -- acceptable for a first-pass guardrail, not production-
    grade.
  - Overconfidence and unsupported-ness are different failure types (see
    Lab 6.1's "Guardrail Interaction Analysis"): a ToT recommendation can
    be well-hedged but still wrong, or overconfident but still correct.
    These guardrails catch tone and grounding separately; neither
    substitutes for the other.
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# Gate 1: Input Sanitisation (pre-generation)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "instruction_id", "amount", "currency", "debtor_name", "debtor_account",
    "debtor_agent_routing", "creditor_name", "creditor_account",
    "creditor_agent_routing", "remittance_information", "purpose_code", "submitted_at",
]

# Deliberately simple pattern set -- catches obvious instruction-injection
# attempts in free-text fields (remittance_information is the only
# free-text field in this pacs.008 subset). Not a substitute for a real
# prompt-injection classifier; disclosed as a first-pass guardrail only.
_INJECTION_PATTERNS = [
    re.compile(r"ignore (all |previous |prior )?instructions", re.IGNORECASE),
    re.compile(r"system\s*:", re.IGNORECASE),
    re.compile(r"<\s*script", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"disregard (the )?(above|previous)", re.IGNORECASE),
]


def check_input_sanitisation(instruction: dict) -> dict:
    """Gate 1: structural completeness + free-text injection scan, run
    BEFORE any pillar reasons over the instruction (not the same as the
    Regulatory pillar's own field validation, which runs later and checks
    scheme-specific rules like the 140-char limit -- this gate is a
    coarser, earlier check)."""
    issues = []

    for f in _REQUIRED_FIELDS:
        if f not in instruction or instruction[f] in (None, ""):
            issues.append(f"missing required field: {f}")

    remit = str(instruction.get("remittance_information", ""))
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(remit):
            issues.append(f"remittance_information contains a suspicious instruction-like pattern: '{pattern.pattern}'")

    return {"proceed": len(issues) == 0, "issues": issues}


# ---------------------------------------------------------------------------
# During-generation: Confidence Calibration (ToT recommendation tone check)
# ---------------------------------------------------------------------------

_OVERCONFIDENCE_MARKERS = {
    "proven": "supported by the available evidence",
    "always": "in most observed cases",
    "never": "rarely",
    "guaranteed": "likely",
    "certainly": "likely",
    "undoubtedly": "the evidence suggests",
    "definitively": "the current evidence indicates",
    "100%": "high confidence",
    "no doubt": "strong indication",
}


def check_confidence_calibration(text: str) -> dict:
    """Scans ToT branch/recommendation text for overconfident language
    before it reaches a human Decider. A near-miss sanctions case is, by
    definition, ambiguous -- a recommendation that reads as certain is a
    calibration problem even if the underlying hypothesis is reasonable."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    flagged = []
    flagged_sentence_indices = set()
    for idx, sentence in enumerate(sentences):
        for marker, hedge in _OVERCONFIDENCE_MARKERS.items():
            if marker in sentence.lower():
                flagged.append({"sentence": sentence.strip(), "marker": marker, "suggested_hedge": hedge})
                flagged_sentence_indices.add(idx)

    score = len(flagged_sentence_indices) / max(len(sentences), 1)
    if score >= 0.5:
        verdict = "FAIL"
    elif score >= 0.3:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return {"verdict": verdict, "overconfidence_score": round(score, 2), "flagged": flagged}


# ---------------------------------------------------------------------------
# Gate 4: Grounded-Citation Requirement (post-generation, Regulatory pillar only)
# ---------------------------------------------------------------------------

def verify_grounded_citation(pillar_result: dict) -> dict:
    """Regulatory Mandates verdicts must cite a retrieved chunk ID + effective
    date; an ungrounded rule-based claim is blocked. Only applies to the
    'regulatory' pillar -- Reach/Sanctions/Fraud verdicts are tool-output-
    grounded by construction, not RAG-grounded, so this check doesn't apply
    to them."""
    if pillar_result["pillar"] != "regulatory":
        return {"verdict": "PASS", "note": "citation check only applies to the Regulatory pillar"}

    if pillar_result["verdict"] == "PASS" and not pillar_result["tool_output"].get("violations"):
        # A clean PASS from field validation alone doesn't require a citation --
        # nothing was asserted about rule text, only that no violations were found.
        return {"verdict": "PASS", "note": "no rule-based claim asserted"}

    if pillar_result.get("citation"):
        return {"verdict": "PASS", "note": f"grounded: {pillar_result['citation']}"}

    return {
        "verdict": "FAIL",
        "note": "Regulatory pillar asserted a violation without an attached citation -- ungrounded, should be blocked per Gate 4",
    }


# ---------------------------------------------------------------------------
# Gate 4: Output Leakage Check (post-generation, deterministic regex)
# ---------------------------------------------------------------------------

# Matches our synthetic account format (SYN-ACCT-#####) as a stand-in for
# what would be a real account-number pattern in production. Intentionally
# simple/deterministic per the Module 6 "deterministic regex scan" spec.
_ACCOUNT_PATTERN = re.compile(r"SYN-ACCT-\d{4,}")


def check_output_leakage(result_text: str) -> dict:
    """Blocks any raw account number from appearing in a recommendation
    before it is released. In this synthetic dataset, account numbers are
    already pseudo-identifiers (SYN-ACCT-#####), not real account data --
    this guardrail still runs so the pattern/mechanism is demonstrated
    correctly, and would carry over directly to a real account-number
    pattern in production."""
    matches = _ACCOUNT_PATTERN.findall(result_text)
    if not matches:
        return {"verdict": "PASS", "redacted_text": result_text, "leaked_accounts": []}

    redacted = _ACCOUNT_PATTERN.sub("[ACCOUNT REDACTED]", result_text)
    return {"verdict": "BLOCKED_AND_REDACTED", "redacted_text": redacted, "leaked_accounts": matches}


# ---------------------------------------------------------------------------
# Composed pipeline
# ---------------------------------------------------------------------------

def run_guardrail_pipeline(instruction: dict, pillar_results: list, final_verdict_text: str) -> dict:
    """Runs all applicable guardrails and returns a combined report. Per
    Lab 6.1's design: conservative by default -- any FAIL blocks or flags
    rather than silently passing."""
    report = {"gates": {}, "overall_verdict": "PASS", "eval_labels": []}

    input_check = check_input_sanitisation(instruction)
    report["gates"]["input_sanitisation"] = input_check
    if not input_check["proceed"]:
        report["overall_verdict"] = "BLOCKED"
        report["eval_labels"].append("input_sanitisation_failed")

    regulatory_result = next((p for p in pillar_results if p["pillar"] == "regulatory"), None)
    if regulatory_result:
        citation_check = verify_grounded_citation(regulatory_result)
        report["gates"]["grounded_citation"] = citation_check
        if citation_check["verdict"] == "FAIL":
            report["overall_verdict"] = "BLOCKED"
            report["eval_labels"].append("ungrounded_regulatory_claim")

    leakage_check = check_output_leakage(final_verdict_text)
    report["gates"]["output_leakage"] = leakage_check
    if leakage_check["verdict"] == "BLOCKED_AND_REDACTED":
        report["eval_labels"].append("output_leakage_redacted")
        if report["overall_verdict"] == "PASS":
            report["overall_verdict"] = "PASS_WITH_REDACTION"

    return report
