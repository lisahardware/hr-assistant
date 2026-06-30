"""
agents/retrieval_agent.py
Queries the local ChromaDB vector store for relevant HR templates
and compliance references. No external API calls — fully local.
"""

from tools.vector_store import VectorStore


class RetrievalAgent:
    """
    Retrieves relevant context from two local collections:
      - hr_templates: owner's document templates indexed from ./templates
      - compliance_sources: public labor law references indexed by compliance_refresh.py
    """

    def __init__(self, store: VectorStore):
        self.store = store

    def retrieve(self, query: str, state: str = None, doc_type: str = None) -> dict:
        """
        Run semantic search over both collections.
        If doc_type is provided, filter templates to only that document type.
        """
        # Map doc_type to template filename for filtering
        template_filter_map = {
            "offer_letter": "offer_letter_template",
            "contractor_agreement": "contractor_agreement_template",
            "onboarding_checklist": "onboarding_checklist_template",
            "job_description": "job_description_template"
        }

        where = None
        if doc_type and doc_type in template_filter_map:
            filename = template_filter_map[doc_type]
            where = {"source": {"$contains": filename}}

        templates = self.store.retrieve_templates(query, n_results=4, where=where)
        compliance = self.store.retrieve_compliance(query, state=state, n_results=4)
        return {
            "templates": templates,
            "compliance": compliance,
            "state": state
        }

    def format_for_context(self, retrieval_result: dict) -> str:
        """
        Format retrieval results into a single context string
        suitable for passing to the drafting agent's prompt.
        Compliance results include source and date so the owner
        can verify the reference if needed.
        """
        lines = []

        if retrieval_result["templates"]:
            lines.append("=== RELEVANT TEMPLATE SECTIONS ===")
            for item in retrieval_result["templates"]:
                source = item["metadata"].get("source", "unknown")
                section = item["metadata"].get("section", "")
                lines.append(f"[Source: {source} | Section: {section}]")
                lines.append(item["content"])
                lines.append("")

        if retrieval_result["compliance"]:
            lines.append("=== COMPLIANCE REFERENCES ===")
            for item in retrieval_result["compliance"]:
                source = item["metadata"].get("source", "unknown")
                state = item["metadata"].get("state", "federal")
                date = item["metadata"].get("retrieved_date", "unknown date")
                lines.append(f"[Source: {source} | State: {state} | Retrieved: {date}]")
                lines.append(item["content"][:800])
                lines.append("")
            lines.append(
                "NOTE: Compliance references are advisory. "
                "Owner should verify before finalizing any legal document."
            )

        return "\n".join(lines) if lines else "No relevant context found in local store."

    def extract_compliance_context(self, retrieval_result: dict) -> str:
        """
        Return compliance text only, used by the critic agent for scoring.
        """
        lines = []
        for item in retrieval_result.get("compliance", []):
            lines.append(item["content"][:800])
        return "\n".join(lines)

    def extract_compliance_notes(self, retrieval_result: dict) -> list[str]:
        """
        Return a list of source + date strings for display in the UI.
        """
        notes = []
        for item in retrieval_result.get("compliance", []):
            source = item["metadata"].get("source", "")
            date = item["metadata"].get("retrieved_date", "unknown date")
            if source:
                notes.append(f"{source} ({date})")
        return notes