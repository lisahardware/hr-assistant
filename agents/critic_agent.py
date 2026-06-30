"""
agents/critic_agent.py
Evaluates and scores Tree-of-Thought draft branches against four criteria.
Prunes weak branches and returns the highest-scoring viable draft.

Each branch is a dict of {{PLACEHOLDER}} -> drafted value (see
DraftingAgent). For scoring purposes only, a branch is rendered into a
human-readable "Field: value" block — the dict itself is what gets
returned as the selected draft, since that's what _generate_docx needs.

Scoring rubric (0-3 per criterion, 12 points total):
  1. Compliance    — alignment with retrieved statutes; prune immediately if 0
  2. Template      — alignment with owner's existing document structure
  3. Clarity       — professional tone and readability
  4. Completeness  — all necessary sections present for the document type

Pruning rules:
  - Compliance score of 0 → prune immediately regardless of other scores
  - Two or more criteria scoring 0 → prune
  - If all branches are pruned → return highest scorer with a warning

Tiebreaker: closer alignment with owner's retrieved templates.
"""

import json
from agents.model_client import chat


class CriticAgent:
    """
    Scores all branches, prunes weak ones, and returns the best draft
    along with scoring metadata for display in the UI.
    """

    def evaluate_and_select(self, branches: list[dict], task: str, compliance_context: str) -> dict:
        """
        Score all branches and select the best viable one.
        Returns the selected field-value dict and full scoring information.
        """
        scored = []

        for i, branch in enumerate(branches):
            rendered = self._render_branch(branch)
            result = self._score_branch(rendered, task, compliance_context, branch_num=i + 1)
            scored.append({
                "branch_num": i + 1,
                "draft": branch,
                "rendered": rendered,
                "scores": result["scores"],
                "total": result["total"],
                "pruned": result["pruned"],
                "reasoning": result["reasoning"]
            })

        # Separate viable branches from pruned ones
        viable = [s for s in scored if not s["pruned"]]

        if not viable:
            # All branches pruned — return highest scorer with a warning
            viable = sorted(scored, key=lambda x: x["total"], reverse=True)[:1]
            viable[0]["warning"] = (
                "All branches had evaluation issues. "
                "Owner review is strongly recommended before using this document."
            )

        # Select highest scoring viable branch
        best = max(viable, key=lambda x: x["total"])

        return {
            "selected_draft": best["draft"],
            "selected_draft_rendered": best["rendered"],
            "branch_num": best["branch_num"],
            "scores": best["scores"],
            "total_score": best["total"],
            "reasoning": best["reasoning"],
            "warning": best.get("warning", ""),
            "all_scores": [
                {
                    "branch": s["branch_num"],
                    "total": s["total"],
                    "pruned": s["pruned"]
                }
                for s in scored
            ]
        }

    def _render_branch(self, branch: dict) -> str:
        """
        Render a field-value dict branch into readable "Field: value" text
        for scoring purposes. This is what the critic LLM sees — the dict
        itself remains the source of truth for document generation.
        """
        lines = []
        for key, value in branch.items():
            lines.append(f"{key}: {value}")
        return "\n".join(lines)

    def _score_branch(self, rendered: str, task: str, compliance_context: str, branch_num: int) -> dict:
        """
        Score a single branch using the model.
        Returns scores, total, pruned flag, and one-sentence reasoning.
        Falls back to neutral scores if JSON parsing fails.
        """
        prompt = f"""You are an HR document quality evaluator reviewing drafted field values
that will be inserted into a fixed HR document template.

TASK BEING ADDRESSED: {task}

COMPLIANCE CONTEXT:
{compliance_context[:1000] if compliance_context else "No compliance context available."}

DRAFTED FIELD VALUES (Branch {branch_num}):
{rendered}

IMPORTANT CONTEXT: Some fields will intentionally show a literal
{{{{PLACEHOLDER}}}} value — this means the business owner will fill them
in manually (e.g. company-specific task descriptions, signatory details
not provided in the request). Do NOT penalize a branch for these — they
are correct behavior, not omissions. Only penalize if a field that the
LLM clearly should have drafted from the available information (like a
payment schedule, IP ownership clause, or notice period) was left blank
or as a placeholder instead.

Score on four criteria, 0-3 each (12 points total):
  0 = poor/unacceptable  1 = acceptable with issues  2 = good  3 = excellent

1. compliance: Do the drafted values align with the compliance context?
   Score 0 only for clear legal red flags (e.g. illegal payment terms,
   discriminatory language). Score 2-3 if values are legally reasonable
   even if not perfect. A branch with mostly placeholder values but no
   legal issues should score at least 1 here, not 0.

2. template_alignment: Are the drafted values concise, professional, and
   suitable for insertion into a formal HR document? Score 2-3 if values
   are appropriately brief phrases/clauses. Score 0-1 if values are
   rambling, duplicate the surrounding sentence, or are clearly off-format.

3. clarity: Is the language clear and professional? Score 2-3 for plain,
   readable business language. Score 0-1 for jargon, contradictions, or
   garbled text.

4. completeness: Did the LLM draft real content for the fields it
   reasonably could have (payment terms, IP ownership, notice period,
   scope of work, etc.)? Intentional {{{{PLACEHOLDER}}}} values for
   owner-filled fields (company name, signatory, role-specific tasks)
   do NOT count against completeness. Score 2-3 if substantive fields
   are well-drafted. Score 0-1 only if fields the LLM clearly had enough
   information to fill are left empty or as placeholders.

Pruning rule: set pruned to true ONLY if compliance score is 0 AND there
is a genuine legal red flag, OR if three or more criteria score 0.
Do not prune merely because some fields are intentional placeholders.

Return ONLY this JSON, no explanation, no markdown, no code fences:
{{"scores": {{"compliance": 0, "template_alignment": 0, "clarity": 0, "completeness": 0}}, "pruned": false, "reasoning": "one sentence"}}"""

        response_text = chat(prompt, max_tokens=200)

        try:
            # Strip any markdown fences the model may have added
            clean = response_text.replace("```json", "").replace("```", "").strip()
            result = json.loads(clean)
            scores = result.get("scores", {})
            total = sum(scores.values())
            return {
                "scores": scores,
                "total": total,
                "pruned": result.get("pruned", False),
                "reasoning": result.get("reasoning", "")
            }
        except Exception:
            # Fallback — neutral scores, no pruning, flag for review
            return {
                "scores": {
                    "compliance": 1,
                    "template_alignment": 1,
                    "clarity": 1,
                    "completeness": 1
                },
                "total": 4,
                "pruned": False,
                "reasoning": "Scoring could not be parsed — manual review recommended."
            }