"""
agents/coordinator_agent.py
Runs the ReAct reasoning loop and orchestrates all other agents.

Workflow:
  1. Classify the user's request into a workflow category
  2. Extract field values mentioned in the request
  3. Retrieve relevant templates and compliance context (RetrievalAgent)
  4. Generate candidate drafts (DraftingAgent — Tree-of-Thought)
  5. Evaluate and select the best draft (CriticAgent)
  6. Format and return the result (CommunicationAgent)

Document workflows use the full ToT pipeline (steps 3-6).
Email drafts, scheduling, and general questions use a simplified path.
"""

import json
import os
from agents.model_client import chat
from agents.retrieval_agent import RetrievalAgent
from agents.drafting_agent import DraftingAgent
from agents.critic_agent import CriticAgent
from agents.communication_agent import CommunicationAgent
from tools.vector_store import VectorStore
from tools.document_creator import DocumentCreator
from tools.mcp_client import MCPClient


# Document types that go through the full ToT pipeline
DOCUMENT_TYPES = {
    "offer_letter",
    "contractor_agreement",
    "onboarding_checklist",
    "job_description"
}

# Map document type to template filename
TEMPLATE_MAP = {
    "offer_letter": "offer_letter_template.docx",
    "contractor_agreement": "contractor_agreement_template.docx",
    "onboarding_checklist": "onboarding_checklist_template.docx",
    "job_description": "job_description_template.docx"
}

# Path to the business's standing details (company name, address, default
# signatory, etc.) — fields that are true on every document, not just the
# current request. Edit this file once; see business_config.example.json
# for the expected shape. Missing file or invalid JSON is treated as "no
# config set" rather than an error, so the app still runs fine without it
# (those fields just fall back to the usual {{PLACEHOLDER}} / drafting
# behavior, same as before this feature existed).
BUSINESS_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "business_config.json")


def _load_business_config() -> dict:
    """
    Load standing business details from BUSINESS_CONFIG_PATH. Keys are
    matched against template placeholders case-insensitively (normalized
    to uppercase here, same convention as everywhere else in this file).
    Returns {} if the file doesn't exist or can't be parsed — this feature
    is additive and optional, never a hard requirement to run the app.
    """
    if not os.path.exists(BUSINESS_CONFIG_PATH):
        return {}
    try:
        with open(BUSINESS_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        return {str(k).upper(): str(v) for k, v in raw.items()}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not load business_config.json ({e}). Continuing without it.")
        return {}


class CoordinatorAgent:
    """
    Main entry point for all user requests.
    Instantiates and coordinates the other four agents.
    """

    def __init__(self):
        self.store = VectorStore()
        self.retrieval = RetrievalAgent(self.store)
        self.drafting = DraftingAgent()
        self.critic = CriticAgent()
        self.mcp = MCPClient()
        self.doc_creator = DocumentCreator()
        self.communication = CommunicationAgent(self.mcp, self.doc_creator)
        self.business_config = _load_business_config()
        if self.business_config:
            print(f"Loaded business config: {list(self.business_config.keys())}")
        else:
            print("No business_config.json found — company/signatory fields will need to be "
                  "provided per-request or filled in manually after generation.")

    def handle_request(self, user_input: str) -> dict:
        """
        Main ReAct loop entry point.
        Classify → Extract → Retrieve → Draft → Evaluate → Respond
        """
        # Step 1: Classify
        category = self._classify(user_input)

        # Step 2: Extract fields
        fields = self._extract_fields(user_input, category)

        # Step 3: Route to appropriate workflow
        if category in DOCUMENT_TYPES:
            return self._document_workflow(category, user_input, fields)
        elif category == "email_draft":
            return self._email_workflow(user_input, fields)
        elif category == "schedule_interview":
            return self._schedule_workflow(user_input, fields)
        else:
            return self._general_question_workflow(user_input)

    # ─────────────────────────────────────────────
    # Classification and field extraction
    # ─────────────────────────────────────────────

    # ----------------------------------
    # The compliance question returned a contractor agreement draft instead of answering the question. 
    # This is a classification issue — Mistral misclassified "What are the key differences between employee vs contractor in Maryland" 
    # as a contractor_agreement instead of general_question. 
    # Smaller local models are less reliable at classification than Claude, so we need to make that prompt more explicit 
    # and add a few examples to guide Mistral.
    # Mistral responds much better to few-shot examples than abstract instructions.
    # ----------------------------------

    def _classify(self, user_input: str) -> str:
        """Classify the request into a workflow category."""
        prompt = f"""You are classifying an HR request into exactly one category.

    Categories and when to use them:
    - offer_letter: user wants to create or draft an offer letter for a new hire
    - contractor_agreement: user wants to create or draft a contractor or subcontractor agreement
    - onboarding_checklist: user wants to create an onboarding checklist for a new employee
    - job_description: user wants to create or draft a job posting or job description
    - email_draft: user wants to draft an email to a candidate or employee
    - schedule_interview: user wants to schedule or set up an interview
    - general_question: user is asking a question about HR, employment law, compliance, or best practices

    Examples:
    - "Draft an offer letter for Jane" → offer_letter
    - "Create a contractor agreement for a consultant" → contractor_agreement
    - "Make an onboarding checklist for a new engineer" → onboarding_checklist
    - "Write a job description for a marketing manager" → job_description
    - "Draft an email to schedule an interview" → email_draft
    - "Schedule an interview with the candidate" → schedule_interview
    - "What is the difference between an employee and contractor?" → general_question
    - "What are Maryland wage laws?" → general_question
    - "How does PTO work for contractors?" → general_question

    Request to classify: {user_input}

    Reply with only the category name, nothing else. Do not explain."""

        result = chat(prompt, max_tokens=20).strip().lower()

        for category in list(DOCUMENT_TYPES) + ["email_draft", "schedule_interview", "general_question"]:
            if category in result:
                return category
        return "general_question"

    def _extract_fields(self, user_input: str, document_type: str) -> dict:
        """Extract field values from the user's request."""
        prompt = f"""Extract field values from this HR request for a {document_type}.

Return a JSON object where:
- KEYS are lowercase with underscores (e.g. "candidate_name", "start_date").
  This underscore formatting applies ONLY to the keys.
- VALUES are copied from the request in normal, natural language — exactly
  as a person would write them, with spaces and normal punctuation. NEVER
  apply underscore or snake_case formatting to a value.

Example:
Request: "Hire Jordan Smith for marketing consulting at $150/hour for 3 months."
Correct JSON: {{"contractor_name": "Jordan Smith", "service_type": "marketing consulting", "rate": "$150/hour", "duration": "3 months"}}
Incorrect JSON (do NOT do this): {{"contractor_name": "Jordan_Smith", "service_type": "marketing_consulting", "duration": "3_months"}}

Only include a field if it is explicitly stated in the request. Do not
invent, guess, or fill in a placeholder-style value (such as "Your Company
Name", "Your Name", "TBD", or similar) for anything not mentioned — simply
leave that field out of the JSON entirely.

Request: {user_input}

Return only raw JSON, no explanation, no markdown, no code blocks."""

        response = chat(prompt, max_tokens=500)
        try:
            clean = response.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
            return self._sanitize_extracted_fields(parsed, user_input)
        except Exception:
            return {}

    def _sanitize_extracted_fields(self, parsed: dict, user_input: str) -> dict:
        """
        Defensive backstop for _extract_fields, independent of how well the
        model follows the prompt instructions:
          1. Drop any value that looks like invented placeholder boilerplate
             (e.g. "Your Company Name", "Your Name", "TBD") rather than a
             real value the user actually provided.
          2. Un-mangle snake_case values back into normal spacing when the
             original request didn't contain an underscore at that spot —
             i.e. the model snake_cased a value it should have left as
             natural language.
          3. Prefer the user's own exact wording when the model silently
             substituted a different form of the same value during
             extraction (e.g. abbreviating "Virginia" to "VA", or
             reformatting a date) — known fields are supposed to be
             copied through verbatim everywhere downstream, so any
             rewriting needs to be caught here, at the source.
        """
        placeholder_phrases = {
            "your company name", "your name", "your title", "tbd",
            "to be determined", "n/a", "not specified", "unknown"
        }

        # US state abbreviation -> full name, used only to detect/undo a
        # likely model-introduced abbreviation; not an exhaustive gazetteer.
        state_abbrev_to_name = {
            "va": "Virginia", "md": "Maryland", "dc": "District of Columbia",
        }

        cleaned = {}
        for key, value in parsed.items():
            if not isinstance(value, str):
                cleaned[key] = value
                continue

            value_str = value.strip()

            # Drop invented placeholder-style boilerplate entirely.
            if value_str.lower() in placeholder_phrases:
                continue

            # If the value contains underscores but the original user
            # request doesn't contain that exact underscored substring,
            # the model likely snake_cased a natural-language phrase —
            # restore normal spacing.
            if "_" in value_str and value_str not in user_input:
                value_str = value_str.replace("_", " ")

            # If the model abbreviated a state name that appeared in full
            # in the user's own text, restore the full name the user
            # actually typed, so it matches consistently everywhere the
            # document references it.
            full_name = state_abbrev_to_name.get(value_str.lower())
            if full_name and value_str not in user_input and full_name in user_input:
                value_str = full_name

            cleaned[key] = value_str

        return cleaned

    # ─────────────────────────────────────────────
    # Document workflow (full ToT pipeline)
    # ─────────────────────────────────────────────

    def _document_workflow(self, doc_type: str, task: str, fields: dict) -> dict:
        """
        Full pipeline: retrieve → draft (ToT) → evaluate → generate .docx
        Used for offer letters, contractor agreements, onboarding checklists,
        and job descriptions.

        The drafting agent now produces field-value dicts (one per branch)
        rather than free-form prose, keyed to the target template's actual
        {{PLACEHOLDER}} set. This guarantees the document the owner reviews
        in the UI and the .docx they download are generated from the exact
        same data — there's no separate "draft text" path that can drift
        out of sync with the template-filling path.
        """
        # Retrieve relevant context
        state = fields.get("state")
        retrieval_result = self.retrieval.retrieve(task, state=state, doc_type=doc_type)
        context = self.retrieval.format_for_context(retrieval_result)
        compliance_context = self.retrieval.extract_compliance_context(retrieval_result)
        compliance_notes = self.retrieval.extract_compliance_notes(retrieval_result)

        # Look up the target template's actual placeholder keys, so drafting
        # is always in lockstep with what the .docx will actually need —
        # never hardcoded, never able to silently drift from the template.
        template_file = os.path.join("./templates", TEMPLATE_MAP.get(doc_type, ""))
        # Look up each placeholder's surrounding sentence context (not just
        # the bare key list) so the drafting agent can see exactly how each
        # blank fits into its sentence — this is what prevents the model
        # from drafting a full restated clause when only a short fragment
        # is needed.
        field_contexts = self.doc_creator.get_placeholder_contexts(template_file)

        # Merge standing business details (company name, default signatory,
        # etc.) in as defaults, with anything explicitly stated in this
        # specific request taking precedence over the standing config —
        # e.g. if the owner usually signs as "Owner" but this request says
        # "have the office manager sign instead," the request wins. Both
        # sides are normalized to uppercase keys before merging so a
        # request field reliably overrides its config counterpart, rather
        # than the two coexisting under different-cased keys until some
        # later normalization step resolves them ambiguously.
        known_fields = {k.upper(): v for k, v in self.business_config.items()}
        known_fields.update({k.upper(): v for k, v in fields.items()})

        # Always inject today's date so the LLM never has to draft it —
        # avoids echoed lead-in artifacts like "Posted: June 29, 2026"
        # when the template sentence is "Posted: {{DATE}}".
        from datetime import date as _date
        if "DATE" not in known_fields:
            known_fields["DATE"] = _date.today().strftime("%B %d, %Y")

        # Generate 3 candidate field-value branches
        branches = self.drafting.generate_branches(
            task, context, field_contexts, known_fields=known_fields, n_branches=3
        )

        # Evaluate and select best branch
        selection = self.critic.evaluate_and_select(branches, task, compliance_context)
        selected_fields = selection["selected_draft"]  # dict of UPPERCASE_KEY -> value

        # Generate the .docx from the winning branch's field values. Merge in
        # known_fields (request fields + business config) last so anything
        # explicitly known always wins over anything the LLM drafted, even
        # though DraftingAgent already does this — belt and suspenders,
        # since this is also what _generate_docx actually writes to disk.
        docx_fields = dict(selected_fields)
        docx_fields.update({k.upper(): str(v) for k, v in known_fields.items()})
        doc_path = self._generate_docx(doc_type, docx_fields)

        # Identify unfilled placeholders — anything still showing the literal
        # "{{KEY}}" pattern in the selected branch counts as missing, same as
        # whatever the template itself still has unaddressed.
        missing = self._find_missing_fields(doc_type, docx_fields)

        # Apply review rules
        requires_review, review_reason = self.communication.should_require_review(doc_type)

        # Render the UI preview from the *same* docx_fields dict used to
        # generate the file, so the on-screen draft and the download can
        # never show different content again.
        preview = self._render_preview(doc_type, docx_fields)

        result = {
            "summary": (
                f"{doc_type.replace('_', ' ').title()} generated using branch "
                f"{selection['branch_num']} of 3 (score: {selection['total_score']}/12)."
            ),
            "draft": preview,
            "document_path": doc_path,
            "requires_review": requires_review,
            "review_reason": review_reason,
            "missing_fields": missing[:10],
            "compliance_notes": compliance_notes,
            "actions_taken": [
                "Retrieved templates from local vector store",
                "Generated 3 draft branches (Tree-of-Thought)",
                f"Selected branch {selection['branch_num']} after critic evaluation"
            ],
            "score_info": selection["all_scores"],
            "warning": selection.get("warning", "")
        }

        return self.communication.format_response(result)

    def _render_preview(self, doc_type: str, fields: dict) -> str:
        """
        Render a plain-text preview of the document for display in the UI,
        built from the exact same field values used to generate the .docx.
        Falls back to a simple field listing if the template can't be read
        for any reason, so the UI never breaks even if preview rendering
        has an issue — the download itself is unaffected either way.
        """
        try:
            template_file = os.path.join("./templates", TEMPLATE_MAP.get(doc_type, ""))
            return self.doc_creator.render_text_preview(template_file, fields)
        except Exception as e:
            print(f"Preview rendering error ({doc_type}): {e}")
            lines = [f"{k}: {v}" for k, v in fields.items()]
            return "\n".join(lines)

    def _generate_docx(self, doc_type: str, fields: dict) -> str | None:
        """Generate a .docx file for the given document type and fields."""
        try:
            if doc_type == "offer_letter":
                return self.doc_creator.create_offer_letter(fields)
            elif doc_type == "contractor_agreement":
                return self.doc_creator.create_contractor_agreement(fields)
            elif doc_type == "onboarding_checklist":
                return self.doc_creator.create_onboarding_checklist(fields)
            elif doc_type == "job_description":
                return self.doc_creator.create_job_description(fields)
        except Exception as e:
            print(f"Document generation error ({doc_type}): {e}")
            return None

    def _find_missing_fields(self, doc_type: str, fields: dict) -> list[str]:
        """
        Return placeholder keys that are still unresolved after drafting —
        i.e. fields whose value is still the literal "{{KEY}}" placeholder
        because neither the user nor the drafting agent could supply a
        value. `fields` here should be the post-draft dict (docx_fields),
        not the raw user input, so this reflects what's actually still
        missing in the generated document rather than what the user
        happened to type.
        """
        template_file = os.path.join("./templates", TEMPLATE_MAP.get(doc_type, ""))
        if not os.path.exists(template_file):
            return []
        all_placeholders = self.doc_creator.list_unfilled_placeholders(template_file)
        upper_fields = {k.upper(): v for k, v in fields.items()}

        missing = []
        for p in all_placeholders:
            value = upper_fields.get(p)
            if value is None or str(value).strip() == f"{{{{{p}}}}}":
                missing.append(p)
        return missing

    # ─────────────────────────────────────────────
    # Email draft workflow
    # ─────────────────────────────────────────────

    def _email_workflow(self, task: str, fields: dict) -> dict:
        """Draft a candidate-facing email. Always flagged for owner approval."""
        retrieval_result = self.retrieval.retrieve(task)
        context = self.retrieval.format_for_context(retrieval_result)

        # Build a business context block from config so the LLM uses real
        # values instead of inventing [COMPANY_NAME] bracket placeholders.
        # Note: deliberately omits COMPANY_ADDRESS — a street address is
        # appropriate for a printed letter, not an email, and including it
        # here previously caused the model to format the email like a
        # physical letter (address block, duplicate salutation) instead.
        config = self.business_config
        business_block = "\n".join([
            f"Company name: {config.get('COMPANY_NAME', '[Company Name]')}",
            f"Sender name: {config.get('SIGNATORY_NAME', '[Your Name]')}",
            f"Sender title: {config.get('SIGNATORY_TITLE', '[Your Title]')}",
            f"Contact email: {config.get('HR_EMAIL', '[HR Email]')}",
        ])

        # Also surface any fields extracted from the request (candidate name,
        # role, interview date, etc.) so Mistral can use them directly.
        request_block = ""
        if fields:
            request_block = "\nDetails from the request:\n" + "\n".join(
                f"  {k}: {v}" for k, v in fields.items()
            )

        prompt = f"""Draft a professional HR email for a small business owner.
Use a clear, professional but warm tone. Do NOT use bracket placeholders
like [COMPANY_NAME] or [Your Name] — use the real values provided below.
For any information not provided (e.g. specific interview date/time),
leave a clear blank line or write "(to be confirmed)" so the owner knows
to fill it in before sending.

FORMAT RULES — this is an EMAIL, not a printed letter:
- Do NOT include a mailing address block (no street address, no "Date:"
  line, no recipient address) — emails do not use these.
- Include exactly ONE greeting line (e.g. "Dear Marcus,") — never repeat
  the salutation.
- Structure: Subject line, blank line, greeting, 2-4 short paragraphs,
  closing, sender's name and title.

BUSINESS DETAILS (use these exactly):
{business_block}
{request_block}

REQUEST: {task}

Relevant context:
{context[:400]}

Write the subject line on the first line starting with "Subject:", then
a blank line, then the full email body through the closing signature."""

        draft = chat(prompt, max_tokens=800)

        return self.communication.format_response({
            "summary": "Email draft ready. Review and approve before sending.",
            "draft": draft,
            "requires_review": True,
            "review_reason": "All candidate-facing emails require owner approval before sending.",
            "actions_taken": ["Generated email draft"],
            "missing_fields": [],
            "compliance_notes": []
        })

    # ─────────────────────────────────────────────
    # Interview scheduling workflow
    # ─────────────────────────────────────────────

    def _schedule_workflow(self, task: str, fields: dict) -> dict:
        """Parse scheduling intent and surface missing details for confirmation."""
        required = ["candidate_email", "start_date", "start_time", "duration"]
        missing = [f for f in required if f not in fields]

        return self.communication.format_response({
            "summary": "Interview scheduling request received.",
            "draft": (
                f"To schedule this interview, please confirm the following details:\n\n"
                f"What was extracted from your request:\n{json.dumps(fields, indent=2)}\n\n"
                f"Still needed: {', '.join(missing) if missing else 'Nothing — ready to schedule.'}"
            ),
            "requires_review": True,
            "review_reason": "Please confirm all details before the calendar invite is sent.",
            "actions_taken": ["Parsed scheduling request"],
            "missing_fields": missing,
            "compliance_notes": []
        })

    # ─────────────────────────────────────────────
    # General question workflow
    # ─────────────────────────────────────────────

    def _general_question_workflow(self, task: str) -> dict:
        """Answer a general HR question using retrieved context."""
        retrieval_result = self.retrieval.retrieve(task)
        context = self.retrieval.format_for_context(retrieval_result)
        compliance_notes = self.retrieval.extract_compliance_notes(retrieval_result)

        prompt = f"""Answer this HR question for a small business owner.
Be practical and clear. If the answer involves legal matters, recommend professional legal review.

Question: {task}

Relevant context from local knowledge base:
{context}"""

        answer = chat(prompt, max_tokens=800)

        return self.communication.format_response({
            "summary": "Here is information relevant to your question.",
            "draft": answer,
            "requires_review": False,
            "actions_taken": ["Retrieved relevant context", "Generated response"],
            "missing_fields": [],
            "compliance_notes": compliance_notes
        })