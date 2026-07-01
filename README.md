# Payments Qualifier Agent

**Part of:** Corporate Agentic Treasury Platform (concept) — CMU Agentic AI Program capstone
**Covers:** A multi-agent, four-pillar pre-flight compliance and validation system for US TCH Real-Time Payments (RTP)
**Status:** MVP prototype — synthetic data, deterministic + hybrid LLM reasoning, not connected to any live system

---

## Table of contents

- [The problem](#the-problem)
- [What this agent does — and doesn't do](#what-this-agent-does--and-doesnt-do)
- [Architecture](#architecture)
- [Design evolution across the program](#design-evolution-across-the-program)
- [Design decisions — MVP vs. target state](#design-decisions--mvp-vs-target-state)
- [Verified vs. illustrative constants](#verified-vs-illustrative-constants)
- [Data contracts](#data-contracts)
- [Synthetic data disclosure](#synthetic-data-disclosure)
- [Repository contents](#repository-contents)
- [How to run](#how-to-run)
- [Sample scenario walkthrough](#sample-scenario-walkthrough)
- [Interactive demos](#interactive-demos)
- [Evaluation](#evaluation)
- [Human-in-the-loop design](#human-in-the-loop-design)
- [Known limitations](#known-limitations)
- [Sibling agents (not built)](#sibling-agents-not-built)
- [Limitations & next steps](#limitations--next-steps)

---

## The problem

Before a payment instruction reaches the internal processing engine for execution and onward submission to the TCH RTP network, it must clear four qualification checks — network reach, regulatory/scheme compliance, OFAC sanctions screening, and fraud pattern detection. Today these checks are performed by separate specialist teams using separate tools, creating delays, coverage gaps, and no consistent pre-flight standard. Because RTP settlement is real-time and irrevocable, a payment that clears without being properly screened cannot be recalled.

The intended users are payments operations and compliance teams at a large financial institution (framed here as a Corporate Agentic Treasury Platform for JPMorgan Chase) who currently perform these checks manually or through disconnected systems before a payment is released to the network.

## What this agent does — and doesn't do

| In scope | Out of scope |
|---|---|
| Four-pillar pre-flight check: Reach, Regulatory Mandates, Sanctions Compliance, Fraud Signals | Executing, routing, or settling any payment |
| Structured PASS / HOLD / ESCALATED recommendation with a per-pillar reason | Any decision-making authority beyond recommendation |
| Grounded citation for Regulatory verdicts (chunk ID + effective date) | Full ISO 20022 pacs.008 schema (this MVP uses a scoped subset — see Data Contracts) |
| Mandatory human escalation for Sanctions near-misses — never auto-resolved | Live TCH/OFAC/BRIE integration (all data here is synthetic) |
| Full audit trail (trace log) for every verdict | The broader Corporate Agentic Treasury Platform (Intelligent Routing, Payment Observability, Liquidity Optimizer, FX Optimizer are named as conceptual siblings only — not built, agent count TBD) |

The Qualifier's verdict is delivered synchronously, inside the RTP response window, because the deterministic pillars complete quickly and any Sanctions near-miss is deliberately pushed *outside* that window by returning a fast HOLD/ESCALATED rather than waiting on human review. A separate downstream system (the processing engine, and conceptually an Intelligent Routing Agent) would consume this verdict — the Qualifier decides, a different system executes.

## Architecture

```
   pacs.008 instruction (synthetic)
              │
              ▼
      ┌───────────────┐
      │  INPUT GATE   │  Gate 1: structural completeness + free-text
      │               │  injection scan, before any pillar reasons
      └───────┬───────┘  over the instruction
              │ pass                    │ blocked
              ▼                         ▼
      ┌───────────────┐         ┌───────────────┐
      │  ORCHESTRATOR │         │   BLOCKED     │──┐
      │  (LangGraph)  │         └───────────────┘  │
      └───────┬───────┘                            │
              │                                     │
              ▼                                     │
      ┌───────────────┐                             │
      │     REACH     │  Deterministic: rtp_participant_lookup →
      │               │  active participant? within cap?
      └───────┬───────┘
              │ PASS                    │ HOLD
              ▼                         │
      ┌───────────────┐                 │
      │  REGULATORY   │  Deterministic: rtp_field_validator +      │
      │               │  regulatory_rule_retriever (RAG, effective-│
      └───────┬───────┘  date filtered) → verdict requires citation│
              │ PASS                    │ HOLD                     │
              ▼                         │                          │
      ┌───────────────┐                 │                          │
      │   SANCTIONS   │  Deterministic: sanctions_screening_tool   │
      └───────┬───────┘  (fuzzy match) → match_score                │
              │                                                     │
     ┌────────┴─────────────────────┐                               │
     │                               │                               │
  clear (<0.65)              near-miss (0.65-0.85)          confirmed match (>0.85)
     │                               │                               │
     │                               ▼                               │
     │                       ┌───────────────┐                       │
     │                       │  ToT (BFS)    │  REAL LLM CALL —      │
     │                       │  Generator →  │  the only one in      │
     │                       │  Critic       │  this MVP             │
     │                       └───────┬───────┘                       │
     │                               │ recommendation only            │
     │                               ▼                                │
     │                       ┌───────────────┐                        │
     │                       │    DECIDER    │  Human sign-off —      │
     │                       │               │  NEVER auto-resolved   │
     │                       └───────┬───────┘                        │
     │                               │                                │
     └───────────────┬───────────────┘                                │
                      ▼                                                │
              ┌───────────────┐                                        │
              │     FRAUD     │  Deterministic: duplicate_payment_     │
              │               │  checker + fraud_pattern_scorer         │
              └───────┬───────┘                                        │
                      │                                                 │
           ┌──────────┴───────────┐                                    │
        all PASS                any HOLD ───────────────────────────────┘
           │                       │
           ▼                       ▼
   ┌───────────────┐       ┌───────────────┐
   │ GUARDRAIL     │       │   VALIDATOR   │  Human — reviews before
   │ CHECK         │       │               │  correction/resubmission
   │ Gate 4: cite- │       └───────────────┘
   │ ation + leak- │
   │ age + ToT     │
   │ calibration   │
   └───────┬───────┘
           │
           ▼
   ┌───────────────┐
   │ FINAL VERDICT │  QualificationResult (PASS / HOLD / ESCALATED_
   │               │  PENDING_DECIDER / BLOCKED_INPUT) — end of MVP
   └───────────────┘  scope. Downstream execution not built.
```

**Design reference, not built:** a Regulatory HOLD on a structural violation (e.g. remittance field too long) could instead route to an Enrichment Agent for automated remediation under a configurable `agentic` vs. `human_in_the_loop` posture. A working sketch of this idea lives in `/reference/enrichment_agent_concept.py`, exercised standalone — it is never imported or called by the actual orchestrator. Reach, Sanctions, and Fraud HOLDs are permanently ineligible for this concept under any mode, by hard design boundary (auto-resolving a compliance judgment call would violate the recommendation-only authority principle).

## Design evolution across the program

The system began in Module 2 as a single ReAct agent — one reasoning loop per pillar, with memory split between a transaction log (duplicate detection) and a vector store (rule retrieval). Module 3 added retrieval specifically where the LLM's own knowledge is stale or unauditable (participant directory, scheme rules), with effective-date metadata filtering to prevent superseded rules from being retrieved. Module 4 identified that three of the four pillars are deterministic, but Sanctions Compliance has a genuinely fuzzy middle — Tree-of-Thought reasoning was scoped narrowly to that one insertion point. Module 5 redesigned the system as five agents (one orchestrator, four scoped specialists) to reduce the hallucination risk of one generalist prompt spanning four compliance domains, and formalized the recommendation-only boundary. Module 6 closed the loop from "reasons well" to "fails safely" — guardrails at defined control points, and human-in-the-loop redesigned from a blocking gate into a confidence-based router.

## Design decisions — MVP vs. target state

| Component | Target state (production design) | This MVP | Why |
|---|---|---|---|
| Orchestration | CrewAI Process.hierarchical | LangGraph StateGraph | No CrewAI implementation exists anywhere in the curriculum materials to build against safely; LangGraph has a tested precedent |
| Reach / Regulatory / Fraud reasoning | LLM-driven ReAct loop per pillar | Deterministic rule evaluation, wrapped in a ReAct-shaped trace | These are fixed-rule checks; a model call adds cost/latency/hallucination surface with no correctness gain |
| Sanctions near-miss reasoning | LLM-driven ToT | Same structure, real LLM calls — the one pillar where reasoning is genuinely warranted | This is the one place fuzzy judgment is the actual task |
| RAG (Regulatory pillar) | Pinecone, full TCH rulebook corpus, semantic embedding retrieval | In-memory store, ~9 synthetic chunks, keyword-match retrieval | Proves the effective-date filtering mechanism without needing a hosted vector DB or real document corpus |
| External data (Reach, Sanctions, Fraud) | Live TCH directory, OFAC API, BRIE | Synthetic in-memory data | No live system access; enables hand-computed expected outcomes |
| Observability | LangSmith | Hand-rolled TraceLogger / TraceEvaluator / EscalationQueue | Avoids an external account dependency in a zero-setup repo |
| Guardrails | Layered pre/during/post-gen + NLI-based semantic verification | Same three-stage shape (Lab 6.1 pattern), regex/Jaccard-based checks | NLI adds a model dependency; regex/Jaccard is the tested, disclosed-limitation precedent |
| Money representation | Decimal-precision currency type | Python float | Simpler for MVP legibility; explicitly not production-safe for real monetary values |
| pacs.008 schema | Full ISO 20022 field set | Scoped subset — only fields the four pillars consume | Keeps the data contract legible |
| Packaging | N/A | Standalone repo, not an installable plugin (Option A) | Deliberate scope decision — see repo history |
| Enrichment Agent (auto-remediation) | Possible extension, not part of the original design | Reference sketch only, never wired into the orchestrator | See Architecture section above |

## Verified vs. illustrative constants

Every hardcoded numeric constant is either verified against a real public source or clearly marked illustrative. Full list with sources: `docs/verified_constants.md`. Highlights: the $10,000,000 RTP network cap (raised from $1,000,000 in Feb 2025) and the 140-character remittance limit are both verified; OFAC match-score thresholds and fraud-scoring weights are illustrative and disclosed as such in code.

## Data contracts

Defined in `src/qualifier/schemas.py`: PaymentInstruction (scoped pacs.008 subset), QualifierState (LangGraph state), PillarResult, TraceEvent, QualificationResult. See that file for the literal, importable definitions.

## Synthetic data disclosure

**Every dataset in this repository is synthetic.** No real bank data, no real OFAC SDN entries, no real customer or transaction data, no real routing numbers tied to actual institutions.

- **Participant directory** — fictional institution names; routing numbers are prefixed 999 (an unallocated ABA prefix block) and verified to fail the ABA routing-number checksum, so they cannot resolve to a real institution even by accident.
- **OFAC watchlist** — entirely fictional names, including the engineered near-miss entries. Not derived from or resembling any real SDN list entry.
- **Transaction log / sample payments** — fabricated originator/beneficiary details.
- **Regulatory rule chunks** — the numeric constants (140-char limit, $10M cap) are real and verified; the chunk text itself is original illustrative language, not copied or paraphrased from TCH's member-gated Operating Rules (which could not be accessed for this project). Each chunk is individually tagged verified or illustrative.

Two fictional names — "Viktor A. Marchetti" and "Daniel R. Osei-Mensah" — deliberately omit the (synthetic) suffix used elsewhere, because appending it would dilute the string-similarity score against the watchlist and break the engineered match bands. This is intentional, not an oversight.

## Repository contents

```
payments-qualifier-agent/
├── README.md
├── requirements.txt
├── docs/
│   └── verified_constants.md
├── src/qualifier/
│   ├── schemas.py
│   ├── guardrails.py
│   ├── logging_observability.py
│   ├── tools/            reach.py, regulatory.py, sanctions.py, fraud.py
│   └── agents/           pillars.py, tot_sanctions.py, orchestrator.py
├── data/                 participants.json, ofac_watchlist.json, transaction_log.json,
│                         regulatory_chunks.json, sample_payments.json, expected_outcomes.json
├── eval/
│   ├── run_eval.py       re-runnable end-to-end evaluation script
│   ├── results.csv
│   ├── results_summary.md
│   └── trace_log.jsonl
├── examples/
│   ├── interactive_demo.html    all 8 scenarios, browser-runnable, no dependencies
│   └── tot_live_demo.html       live LLM call demo (Anthropic API, needs a key when self-hosted)
├── reference/
│   └── enrichment_agent_concept.py   NOT wired into the orchestrator — see Architecture section
└── notebook/
    └── payments_qualifier_prototype.ipynb   Colab entry point
```

## How to run

**Option A — Google Colab (recommended, no local setup):**
1. Upload the whole repo folder to Colab (or mount from Google Drive / clone from GitHub once pushed)
2. Open notebook/payments_qualifier_prototype.ipynb
3. Run all cells. Set ANTHROPIC_API_KEY as a Colab secret if you want the Sanctions ToT branch to make a real LLM call — without it, near-miss cases still escalate correctly, just without live ToT reasoning (see orchestrator.py's graceful-degradation note)

**Option B — Local:**
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...   # optional, see above
python eval/run_eval.py
```

## Sample scenario walkthrough

**PASS — SCN-01, happy path:** $4,500 from Cormorant Data Systems Inc to Fenwick Analytics Partners via an active, in-cap participant. Reach PASS → Regulatory PASS (clean fields) → Sanctions PASS (score 0.386, no match) → Fraud PASS (no duplicate, low risk score) → **final: PASS**.

**HOLD — SCN-05, confirmed sanctions match:** Beneficiary name "Viktor A. Marchetti" is an exact string match to watchlist entry SYN-SDN-0001. Reach PASS → Regulatory PASS → Sanctions HOLD (score 1.0, confirmed match) → Fraud not run (fail-fast) → **final: HOLD, escalated to Validator**.

**ESCALATED — SCN-06, sanctions near-miss:** Beneficiary "Daniel R. Osei-Mensah" scores 0.8 against watchlist entry "Daniel R. Osei" — inside the 0.65-0.85 near-miss band. Triggers ToT (Generator/Critic), which produces a recommendation, not a resolution — **final: ESCALATED_PENDING_DECIDER**, never auto-resolved to PASS or HOLD.

See examples/interactive_demo.html to run all 8 scenarios yourself.

## Interactive demos

- **examples/interactive_demo.html** — all 8 core scenarios, pillar-by-pillar trace, runs in any browser, embeds real (not simulated) tool output captured from the actual Python modules.
- **examples/tot_live_demo.html** — makes two real Anthropic API calls (Generator + Critic) using the exact prompts from tot_sanctions.py, for the SCN-06 near-miss case. Requires an API key when self-hosted outside claude.ai's artifact proxy.

## Evaluation

Run eval/run_eval.py to regenerate results. Full methodology and results: eval/results_summary.md.

**Headline results:** 8/8 core scenarios matched hand-computed ground truth (data/expected_outcomes.json, written *before* any pipeline code existed). 8/8 passed the 5-Metric Rule evaluator (Context Adherence, Action Advancement, Custom Code Eval, Escalation-Routing Accuracy, Latency — exact metric set specified in the Module 6 capstone doc).

**Disclosure: the SCN-06 (Sanctions near-miss) result in the committed eval/results.csv and results_summary.md was generated using a mock LLM client, not a live Anthropic API call**, because no API key was available in the environment this was built in. The ToT prompt/parsing logic was separately validated against a real model via examples/tot_live_demo.html (two genuine API calls, real model-generated hypotheses and scores) — but that validation run and the committed evaluation run are not the same execution. If you re-run eval/run_eval.py with a real anthropic.Anthropic() client substituted for MockClient, SCN-06's specific recommendation text will differ from what's committed here, though the escalation behavior (never auto-resolving) is enforced independently of the LLM's output and will not change.

**Escalation queue context:** 7/8 scenarios in this set escalate (6 to Validator, 1 to Decider). This is a stress-test scenario mix, deliberately constructed so every pillar/branch gets exercised — it is not a claim about real-world HOLD rates.

## Human-in-the-loop design

| Trigger | Human role |
|---|---|
| Sanctions near-miss (0.65-0.85 band) | Decider — reviews ToT branches, selects/confirms resolution |
| Any HOLD verdict (Reach, Regulatory, Sanctions confirmed match, Fraud) | Validator — reviews before correction/resubmission |
| Blocked input (Gate 1) | Validator |

## Known limitations

Disclosed in full in eval/results_summary.md; summarized here:
- The output-leakage guardrail (Custom Code Eval) has never been triggered by real pipeline output — verified correct in isolation only, since no pillar's reason text happens to contain an account number in this scenario set.
- The tool-call loop detector (Action Advancement) has never been triggered — the deterministic tools never retry.
- Latency figures are sandbox execution time, not a production SLA measurement.
- Fraud-scoring weights and the OFAC match-score bands are untuned, illustrative values (disclosed in code).
- The post-generation claim-verification approach (Jaccard-style similarity, inherited from Lab 6.1's guardrail pattern) cannot detect semantic inversions — e.g. "is an active participant" vs. "is NOT an active participant" would score as similar.

## Sibling agents (not built)

Named in the broader Corporate Agentic Treasury Platform concept, referenced for context only: Intelligent Routing Agent (would consume this agent's PASS verdicts), Payment Observability Agent, Liquidity Optimizer, FX Optimizer. Total platform agent count is undefined and out of scope here.

## Limitations & next steps

See the Design Decisions table above — each "target state" cell is effectively a next step: real CrewAI orchestration, LangSmith observability, live TCH/OFAC/BRIE integration, a production vector store with the full rulebook corpus, decimal-precision money handling, and (if pursued) a real Enrichment Agent built out from the reference sketch with human sign-off on the auto-remediation boundary.
