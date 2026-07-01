"""Sanctions Compliance near-miss Tree-of-Thought branch.

Only invoked when sanctions_screening_tool returns a match_score in the
0.65-0.85 near-miss band. This is the ONE place in the MVP that makes a
real LLM call, per the locked hybrid design: Reach/Regulatory/Fraud are
fixed-rule checks with no genuine ambiguity; a near-miss sanctions match
is a real judgment call (is this the same person, or a coincidental name
match?) that benefits from a model reasoning over the limited evidence
available.

Adapted from tree_of_thought_agent_construction_solution.ipynb's three
roles (ThoughtGenerator / Critic / DecisionMaker), applied to sanctions
interpretation instead of the Game of 24. Decision strategy is
breadth-first (generate all branches at once, evaluate them all, pick
the best) per the Module 4/5 BFS design decision -- there is no
multi-depth search here since a near-miss interpretation is a single-
round branching problem, not a multi-step search space.

CRITICAL DESIGN CONSTRAINT: the surviving branch from this module is a
RECOMMENDATION only. Per Module 6 ("Mandatory Near-Miss Escalation"),
this module's output NEVER auto-resolves to PASS or HOLD -- it always
escalates to a human Decider, who receives the branches and the
recommendation as decision support. See orchestrator.py for how this
output is packaged into an escalation record.

VERIFICATION STATUS: the JSON-parsing and branch-selection logic below
was tested against a mock LLM client (see the __main__ block) since no
live Anthropic API key was available in the environment this was built
in. The actual API call syntax (anthropic.Anthropic().messages.create)
is standard SDK usage but has NOT been exercised against a real API key
by me -- this should be the first thing verified when this code is
actually run in Colab with a real key.
"""
from __future__ import annotations
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_MODEL = "claude-sonnet-5"  # override via ANTHROPIC_MODEL env var if needed


@dataclass
class Thought:
    branch_id: str
    hypothesis: str
    reasoning: str
    critic_score: float = 0.0


@dataclass
class ToTResult:
    branches: list[Thought]
    recommended_branch: Thought
    escalation_summary: str
    trace_events: list[dict] = field(default_factory=list)


class ThoughtGenerator:
    """Role 1: proposes competing interpretations of the near-miss match."""

    def __init__(self, client, model: str = DEFAULT_MODEL, num_branches: int = 3):
        self.client = client
        self.model = model
        self.num_branches = num_branches

    def generate(self, creditor_name: str, matched_entity: str, match_score: float) -> list[Thought]:
        prompt = f"""You are assisting a sanctions compliance analyst reviewing a near-miss OFAC-style watchlist match. Available evidence is limited to names only -- no date of birth, address, or account history is available in this payment instruction.

Beneficiary on the payment instruction: "{creditor_name}"
Closest watchlist entry: "{matched_entity}"
Name-similarity score: {match_score} (near-miss band: 0.65-0.85)

Generate exactly {self.num_branches} distinct, plausible hypotheses for what this match represents. For each, give a short hypothesis label and one sentence of reasoning grounded only in the name evidence given (do not invent additional facts like DOB or address that are not provided).

Respond with ONLY a JSON array, no other text, in this exact format:
[{{"hypothesis": "...", "reasoning": "..."}}, ...]"""

        response = self.client.messages.create(
            model=self.model, max_tokens=500, temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text.strip()
        if "```" in content:
            content = content.split("```")[1].replace("json", "", 1).strip()

        parsed = json.loads(content)
        return [
            Thought(branch_id=f"B{i+1}", hypothesis=p["hypothesis"], reasoning=p["reasoning"])
            for i, p in enumerate(parsed[: self.num_branches])
        ]


class Critic:
    """Role 2: scores each branch's plausibility given the limited evidence."""

    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model

    def evaluate(self, branches: list[Thought], creditor_name: str, matched_entity: str, match_score: float) -> list[Thought]:
        branch_text = "\n".join(f"{b.branch_id}: {b.hypothesis} -- {b.reasoning}" for b in branches)
        prompt = f"""You are a second-opinion reviewer scoring hypotheses about a sanctions near-miss match.

Beneficiary: "{creditor_name}" | Closest watchlist entry: "{matched_entity}" | Similarity score: {match_score}

Hypotheses to score (0.0 = poorly supported, 1.0 = well supported by the name evidence alone):
{branch_text}

Respond with ONLY a JSON object mapping branch_id to a numeric score, no other text, e.g.:
{{"B1": 0.4, "B2": 0.7, "B3": 0.3}}"""

        response = self.client.messages.create(
            model=self.model, max_tokens=200, temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text.strip()
        if "```" in content:
            content = content.split("```")[1].replace("json", "", 1).strip()
        scores = json.loads(content)

        for b in branches:
            b.critic_score = float(scores.get(b.branch_id, 0.0))
        return branches


class DecisionMaker:
    """Role 3: breadth-first selection -- all branches were already generated and
    scored together (breadth-first, not depth-first search); this role just
    picks the top-scoring branch and packages the escalation summary. This
    role is deterministic, no LLM call."""

    def select(self, branches: list[Thought]) -> Thought:
        return max(branches, key=lambda b: b.critic_score)


def run_sanctions_tot(
    creditor_name: str, matched_entity: str, match_score: float, client, trace_id: str,
    model: str = DEFAULT_MODEL,
) -> ToTResult:
    events = []
    events.append({
        "trace_id": trace_id, "step_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(), "pillar": "sanctions",
        "action_type": "plan",
        "thought_summary": f"Near-miss band (score {match_score}) vs '{matched_entity}' -- invoking ToT",
        "tool_name": None, "tool_input": None, "tool_output_preview": None,
        "eval_labels": [], "error": None,
    })

    generator = ThoughtGenerator(client, model=model)
    branches = generator.generate(creditor_name, matched_entity, match_score)
    events.append({
        "trace_id": trace_id, "step_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(), "pillar": "sanctions",
        "action_type": "tool_call", "thought_summary": f"Generated {len(branches)} branches (BFS)",
        "tool_name": "ThoughtGenerator", "tool_input": None,
        "tool_output_preview": str([b.hypothesis for b in branches])[:200],
        "eval_labels": [], "error": None,
    })

    critic = Critic(client, model=model)
    branches = critic.evaluate(branches, creditor_name, matched_entity, match_score)
    events.append({
        "trace_id": trace_id, "step_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(), "pillar": "sanctions",
        "action_type": "tool_call", "thought_summary": "Critic scored all branches",
        "tool_name": "Critic", "tool_input": None,
        "tool_output_preview": str([(b.branch_id, b.critic_score) for b in branches]),
        "eval_labels": [], "error": None,
    })

    decision_maker = DecisionMaker()
    recommended = decision_maker.select(branches)

    escalation_summary = (
        f"Sanctions near-miss (score {match_score}) between beneficiary '{creditor_name}' and "
        f"watchlist entry '{matched_entity}'. ToT explored {len(branches)} branches. "
        f"Highest-scored recommendation: '{recommended.hypothesis}' (critic score "
        f"{recommended.critic_score}) -- {recommended.reasoning} "
        f"THIS IS A RECOMMENDATION ONLY. Per policy, this case requires mandatory human "
        f"Decider sign-off and must not be auto-resolved."
    )
    events.append({
        "trace_id": trace_id, "step_id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(), "pillar": "sanctions",
        "action_type": "verdict", "thought_summary": escalation_summary,
        "tool_name": None, "tool_input": None, "tool_output_preview": None,
        "eval_labels": [], "error": None,
    })

    return ToTResult(
        branches=branches, recommended_branch=recommended,
        escalation_summary=escalation_summary, trace_events=events,
    )
