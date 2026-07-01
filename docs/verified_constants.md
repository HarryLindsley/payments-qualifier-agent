# Verified Constants

Every numeric constant hardcoded into this system's tools falls into one of two categories: **verified** (checked against a real, cited public source at the time this repo was built, July 2026) or **illustrative** (invented for this demonstration, clearly marked wherever it appears). This file lists every verified constant in one place, for auditability.

| Constant | Value | Used in | Source |
|---|---|---|---|
| RTP network per-transaction cap | $10,000,000 USD | tools/reach.py (network_transaction_cap) | The Clearing House raised the RTP network per-transaction limit from $1,000,000 to $10,000,000, effective February 2025. Confirmed via public reporting (The Clearing House, Payments Dive) during this project's research phase. |
| RTP network per-transaction cap (superseded) | $1,000,000 USD | data/regulatory_chunks.json (RULE-CAP-001, superseded by RULE-CAP-002) | Same source as above -- used specifically to demonstrate the effective-date filtering mechanism in the Regulatory pillar's RAG retrieval, with a real historical rule change rather than an invented one. |
| Unstructured remittance information limit | 140 characters per instance | tools/regulatory.py (REMITTANCE_CHAR_LIMIT) | Confirmed against the ISO 20022 RTP pacs.008 message usage guidelines (July 2017 v1.1). |
| Unstructured remittance information limit (real, more permissive figure) | Up to 3 x 140 = 420 characters across repeated instances | Disclosed only, NOT what this system enforces -- see IMPLEMENTATION_SIMPLIFICATION_FLAG in data/regulatory_chunks.json, chunk RULE-REMIT-001 | Confirmed via a later companion ISO 20022 message specification (May 2025). This system deliberately enforces the stricter single-instance limit, matching the original Module 5 capstone design -- a disclosed scope simplification, not a factual error in the 140-char figure itself. |
| RTP is credit-push only, no debit-pull | N/A (structural fact) | data/regulatory_chunks.json (RULE-IRREVOC-001); informs Fraud Signals pillar design (no pull-based fraud vector) | Confirmed via public RTP network documentation. |
| RTP settlement is final and irrevocable | N/A (structural fact) | Informs the entire Qualifier design rationale (Report Q2/Q3) | Confirmed via public RTP network documentation. |
| pacs.008 message header permits exactly one Credit Transfer Transaction Information block (no batching) | N/A (structural fact) | data/regulatory_chunks.json (RULE-ISO-001) | Confirmed against the public ISO 20022 RTP pacs.008 message specification. |

## Illustrative-only constants (not verified, disclosed at point of use)

These are invented for the MVP and should not be treated as real TCH policy:

- Institution-specific reduced cap ($2,000,000 for the synthetic "Ashgrove Mercantile Bank") -- data/participants.json
- OFAC near-miss confidence band (0.65-0.85) and confirmed-match threshold (0.85) -- plausible illustrative values, not sourced from any real OFAC/TCH publication
- Fraud pattern scoring weights and 0.65 threshold -- tools/fraud.py, explicitly disclosed as untuned in the code docstring
- All Regulatory rule chunks tagged "grounding": "illustrative" in data/regulatory_chunks.json -- TCH's full member-gated Operating Rules and Bulletins could not be retrieved for this project; illustrative chunks are original text, not paraphrased from any real document

## What was NOT independently verified

TCH's full RTP Operating Rules, Operating Bulletins, and Technical Specification documents sit behind member/account access. This project could not retrieve or verify their complete text. Every claim above is limited to what could be confirmed via public sources during the research phase of this project (through July 2026) -- treat anything not listed here as illustrative, even if it reads as plausible.
