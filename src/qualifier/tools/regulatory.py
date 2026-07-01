"""Regulatory Mandates pillar tools: rtp_field_validator + regulatory_rule_retriever.

rtp_field_validator is a deterministic structural check (field presence,
character limits) -- fixed constants, no LLM needed.

regulatory_rule_retriever is the RAG piece: retrieves rule chunks from the
synthetic vector store, filtered by effective_date so a superseded rule is
never surfaced. This is where a citation gets attached to the verdict.

IMPORTANT SIMPLIFICATION (see data/regulatory_chunks.json,
RULE-REMIT-001.IMPLEMENTATION_SIMPLIFICATION_FLAG): this MVP enforces a
single-instance, 140-character remittance limit. The real spec permits up
to 3 x 140 = 420 characters across repeated instances. This is a
deliberate scope simplification, matching the original Module 5 design,
not a factual error in the underlying 140-char figure.
"""
from __future__ import annotations
from typing import TypedDict
from datetime import date

REMITTANCE_CHAR_LIMIT = 140  # verified; see docs/verified_constants.md

REQUIRED_FIELDS = [
    "instruction_id", "amount", "currency", "debtor_name", "debtor_account",
    "debtor_agent_routing", "creditor_name", "creditor_account",
    "creditor_agent_routing", "remittance_information", "purpose_code", "submitted_at",
]


class FieldValidationResult(TypedDict):
    verdict: str            # "PASS" or "HOLD"
    violations: list[str]


def rtp_field_validator(instruction: dict) -> FieldValidationResult:
    """Deterministic structural validation of a pacs.008 instruction subset."""
    violations: list[str] = []

    remit = instruction.get("remittance_information", "") or ""
    if len(remit) > REMITTANCE_CHAR_LIMIT:
        violations.append(
            f"remittance_information is {len(remit)} chars, exceeds the "
            f"{REMITTANCE_CHAR_LIMIT}-char limit (chunk RULE-REMIT-001)"
        )

    for field in REQUIRED_FIELDS:
        if field not in instruction or instruction[field] in (None, ""):
            violations.append(f"missing required field: {field}")

    return {"verdict": "HOLD" if violations else "PASS", "violations": violations}


class RetrievedChunk(TypedDict):
    chunk_id: str
    source_doc: str
    effective_date: str
    grounding: str
    text: str


def regulatory_rule_retriever(topic_keywords: list[str], as_of_date: str, chunk_store: dict) -> list[RetrievedChunk]:
    """Very simple keyword-match retrieval over the synthetic chunk store,
    filtered so any chunk superseded before `as_of_date` is excluded.

    This is intentionally simple (keyword overlap, not embeddings) for the
    MVP -- the point being demonstrated is the effective-date filtering
    mechanism, not retrieval quality. Production target uses semantic
    embedding retrieval against Pinecone (see README).
    """
    as_of = date.fromisoformat(as_of_date)
    results: list[RetrievedChunk] = []

    for chunk in chunk_store["chunks"]:
        eff_date = date.fromisoformat(chunk["effective_date"])
        if eff_date > as_of:
            continue  # not yet effective as of the instruction date

        # Exclude a chunk if a newer version (that supersedes it) is already effective
        superseded_by = chunk.get("superseded_by")
        if superseded_by:
            newer = next((c for c in chunk_store["chunks"] if c["chunk_id"] == superseded_by), None)
            if newer and date.fromisoformat(newer["effective_date"]) <= as_of:
                continue  # a newer version has taken effect; skip the old one

        text_lower = chunk["text"].lower()
        if any(kw.lower() in text_lower for kw in topic_keywords):
            results.append({
                "chunk_id": chunk["chunk_id"],
                "source_doc": chunk["source_doc"],
                "effective_date": chunk["effective_date"],
                "grounding": chunk["grounding"],
                "text": chunk["text"],
            })

    return results
