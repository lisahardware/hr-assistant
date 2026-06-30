"""
agents/drafting_agent.py
Generates multiple candidate field-value sets (Tree-of-Thought branches).
Uses retrieved context as the shared starting point for all branches.

Each branch is a dict of {{PLACEHOLDER}} -> drafted value, matching the
template's existing token set exactly. This keeps document structure
(headings, section order, formatting) fixed and owned by the .docx
template, while letting the LLM vary language/content choices per field.

ToT configuration:
  Branching factor: 3 (three distinct drafting approaches per request)
  Beam: top 2 retained after critic scoring
  Depth: 4 (four major document sections evaluated per branch)
"""

import json
import re

from agents.model_client import chat


class DraftingAgent:
    """
    Generates n_branches distinct field-value sets for a document.
    Each branch makes different language choices for the same set of
    template fields, giving the critic agent meaningful variation to
    evaluate and score, without altering document structure.
    """

    def generate_branches(self, task: str, context: str, field_contexts: dict,
                           known_fields: dict | None = None, n_branches: int = 3) -> list[dict]:
        """
        Generate n_branches candidate field-value sets for the given task.

        Args:
            task: natural-language description of what's being requested.
            context: retrieved template/compliance context.
            field_contexts: dict mapping each {{PLACEHOLDER}} key to the
                literal sentence(s) it appears in within the template, e.g.
                {"PAYMENT_TERMS": 'Payment will be made {{PAYMENT_TERMS}}
                upon receipt of invoice.'}. Showing the model the field
                in its actual sentence — rather than just the bare key
                name — is what keeps it from drafting a full restated
                clause when only a short fragment is needed. Get this from
                DocumentCreator.get_placeholder_contexts(template_path).
            known_fields: values already supplied by the user/coordinator
                (e.g. candidate_name, role, salary — typically lowercase keys
                from _extract_fields). Matched case-insensitively against
                field_contexts and used as-is rather than invented by the LLM.
            n_branches: number of distinct branches to generate.

        Returns:
            A list of dicts, one per branch, each mapping every key in
            field_contexts to a drafted string value. Fields the LLM has no
            information for are left as the literal "{{KEY}}" placeholder
            so missing-field detection downstream keeps working unchanged.
        """
        # Normalize known_fields keys to uppercase so they line up with the
        # template's {{UPPERCASE}} placeholder convention regardless of the
        # casing they arrived in (e.g. from _extract_fields).
        known_fields = {k.upper(): v for k, v in (known_fields or {}).items()}
        field_keys = list(field_contexts.keys())
        branches = []

        for i in range(n_branches):
            style = self._branch_style(i)
            prompt = self._build_prompt(task, context, field_contexts, known_fields, style)

            raw = chat(prompt, max_tokens=2500)
            branches.append(self._parse_fields(raw, field_contexts, known_fields))

        return branches

    def _build_prompt(self, task: str, context: str, field_contexts: dict,
                       known_fields: dict, style: str) -> str:
        known_block = "\n".join(f"- {k}: {v}" for k, v in known_fields.items()) or "(none provided)"

        fields_block_lines = []
        for key, sentence in field_contexts.items():
            if key in known_fields:
                continue
            display_sentence = sentence
            for other_key, other_value in known_fields.items():
                display_sentence = display_sentence.replace(
                    f"{{{{{other_key}}}}}", str(other_value)
                )
            # For sparse checklist items (just "[ ] {{FIELD_NAME}}" with no
            # surrounding words), add the key name itself reformatted as a
            # plain English hint, so the model has something to anchor on
            # beyond just the bare checkbox marker. This avoids the "I have
            # no context so I'll leave it blank" fallback for fields that
            # are genuinely drafting opportunities, not genuinely unknown data.
            bare_placeholder = re.match(
                r"^\[ \] \{\{" + re.escape(key) + r"\}\}$", display_sentence.strip()
            )
            if bare_placeholder:
                hint = key.replace("_", " ").lower()
                display_sentence = f'[ ] {{{{{key}}}}} (draft a specific {hint} relevant to the role and task)'
            fields_block_lines.append(
                f'- {key} — fills the remaining blank in: "{display_sentence}"'
            )
        fields_block = "\n".join(fields_block_lines) or "(all fields already known — see above)"

        return f"""You are an HR document drafting specialist for a small business.

TASK: {task}

RETRIEVED CONTEXT (compliance and reference material — ground your language in this):
{context}

ALREADY-KNOWN FIELD VALUES (use these exactly, do not change them):
{known_block}

DRAFTING APPROACH FOR THIS VERSION: {style}

For each field below, you are filling in ONE BLANK inside an existing
sentence from the document template. The sentence is shown so you can see
exactly how your answer will be inserted. Your value must be ONLY the
fragment that replaces the blank — never the whole sentence, never a
restatement of the sentence, and never a new sentence of your own.

Fields to draft:
{fields_block}

Important rules:
- Respond with ONLY a single JSON object, no preamble, no markdown code fences.
- Keys must exactly match the field names listed above (case-sensitive).
- Each value must be a short word, phrase, or clause fragment — NEVER a
  full sentence, and NEVER text that repeats or paraphrases ANY part of
  the sentence shown for that field, including text that comes BEFORE or
  AFTER the blank. For example, given the sentence "Payment will be made
  {{{{PAYMENT_TERMS}}}} upon receipt of invoice," a correct value is
  "on a biweekly basis" — NOT "Payment will be made on a biweekly basis"
  (repeats the lead-in) and NOT "on a biweekly basis upon receipt of
  invoice" (repeats the trailing text). Only the words that go inside the
  blank itself — nothing from either side of it.
- For fields about ownership, rights, or obligations (e.g. who owns IP),
  return only the noun phrase that fills the blank (e.g. "the Company" or
  "the Contractor") — do not draft a competing or contradictory clause.
- For fields you have no information for and cannot reasonably draft (e.g. a
  specific manager's name that was never provided), return the literal
  string "{{{{FIELD_NAME}}}}" with that exact field's name inside the double
  braces — do not invent a different placeholder format, and do not invent
  generic filler text like "Your Title" or "Your Name."
- Some fields (often checklist items) show very little surrounding text —
  just "[ ] {{{{FIELD_NAME}}}}" with no descriptive sentence. For these,
  look back at the broader TASK description above (e.g. the role, seniority,
  or department mentioned there) to draft something genuinely useful rather
  than leaving it as a placeholder. For example, if the task mentions
  onboarding a Project Manager, a good value for a sparse field like
  ROLE_SPECIFIC_TASK_1 is something concrete like "Review active project
  timelines and current sprint status" — not a placeholder, and not a
  generic phrase like "complete role-specific tasks" that doesn't actually
  say anything. Only fall back to the literal placeholder if the TASK
  description gives you nothing to reasonably work from even for this
  broader context (e.g. the role itself was never mentioned anywhere).
- Some sentences show another field's value already filled in next to your
  blank (e.g. "pay Contractor $150/hour {{{{RATE_TYPE}}}} for services").
  Do not repeat words that are already present in that filled-in text, even
  if the word appears inside a combined token like "$150/hour" rather than
  as its own separate word — "$150/hour" already contains "hour", so a
  RATE_TYPE value of "hour" or "per hour" would be redundant. In this exact
  example, the correct RATE_TYPE value is an empty string "" since the rate
  field already fully describes the unit.
- For numbered series fields (e.g. RESPONSIBILITY_1, RESPONSIBILITY_2,
  REQUIRED_QUALIFICATION_1, etc.), draft a distinct value for EVERY field
  in the series — do not leave any blank or as a placeholder just because
  you already filled the first one. Each item in a series should be a
  different, specific bullet-point appropriate for the role.
- Draft ONLY values relevant to the document type in TASK. Do not include
  content for a different document type.
- Do not include compliance source citations inside any field value — those
  are handled separately.

JSON response:"""

    def _parse_fields(self, raw: str, field_contexts: dict, known_fields: dict) -> dict:
        """
        Parse the LLM's JSON response into a clean field dict. Falls back to
        leaving a literal placeholder for any field that's missing or whose
        response wasn't valid JSON, so a malformed branch degrades gracefully
        instead of crashing the whole pipeline.

        Also applies a defensive trim: if a drafted value still echoes the
        template's own lead-in or trailing text around the blank (despite
        the prompt instructing against it — smaller local models don't
        always follow this perfectly), that overlap is stripped before the
        value is used. This is a safety net, not a substitute for the
        prompt instructions — it only catches verbatim-or-near-verbatim
        repetition of the template's own wording, not general quality
        issues.
        """
        parsed = {}
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = {}

        # Normalize parsed keys to uppercase too — LLM output casing isn't
        # 100% guaranteed even when explicitly instructed.
        parsed = {k.upper(): v for k, v in parsed.items()}

        result = {}
        for key, sentence in field_contexts.items():
            if key in known_fields:
                result[key] = str(known_fields[key])
            elif key in parsed and parsed[key] is not None:
                # Key is present in the LLM's response. An empty string is
                # a valid, intentional answer here (the model determined
                # this field is redundant with already-known neighboring
                # text — see the RATE/RATE_TYPE example in the prompt) and
                # must be preserved as-is, not treated as "no value given."
                value = str(parsed[key])
                if value.strip() == "":
                    result[key] = ""
                    continue
                # Strip a leading colon/semicolon the model sometimes
                # prepends when completing a labelled field — e.g. drafting
                # ": Marketing team" for "Meet with key stakeholders:
                # {{KEY_STAKEHOLDERS}}", producing "stakeholders: : Marketing
                # team". Only strip colons/semicolons, not dashes or periods
                # which appear legitimately in dates and list-item values.
                value = re.sub(r"^[\s:;]+", "", value).strip()
                if not value:
                    result[key] = ""
                    continue
                if self._looks_like_invented_placeholder(value):
                    result[key] = f"{{{{{key}}}}}"
                elif "EMAIL" in key and re.match(r"[^@]+@[^@]+\.[^@]+", value):
                    # Normalize email addresses derived from a person's name —
                    # the LLM inconsistently formats these (sarahlee@ vs
                    # sarah.lee@ vs sarah_lee@ etc.), so if we have the
                    # person's name as a known field, derive the local part
                    # deterministically: firstname.lastname, all lowercase,
                    # no spaces or special chars. Keep the domain part from
                    # the LLM's value since that's template/config-driven and
                    # we don't want to override it.
                    result[key] = self._normalize_email(value, known_fields)
                else:
                    # Substitute known neighboring fields into the sentence
                    # before trimming, so redundancy against an already-
                    # filled-in neighbor (e.g. "$150/hour" + drafted "hour")
                    # is caught the same way as redundancy against the
                    # template's own static wording.
                    display_sentence = sentence
                    for other_key, other_value in known_fields.items():
                        display_sentence = display_sentence.replace(
                            f"{{{{{other_key}}}}}", str(other_value)
                        )
                    trimmed = self._trim_echoed_context(value, display_sentence, key)
                    if self._is_redundant_with_neighbor(trimmed, display_sentence, key):
                        trimmed = ""
                    # Re-run the colon strip after trimming — the trim can
                    # expose a leading colon that wasn't there before (e.g.
                    # "Posted: June 29" → trim removes "Posted" → ": June 29"
                    # → needs another colon strip to get to "June 29").
                    if trimmed:
                        trimmed = re.sub(r"^[\s:;]+", "", trimmed).strip()
                    result[key] = trimmed
            else:
                result[key] = f"{{{{{key}}}}}"

        return result

    def _normalize_email(self, drafted_email: str, known_fields: dict) -> str:
        """
        Normalize an LLM-drafted email address to a consistent
        firstname.lastname@domain format, using the known person's name
        to derive the local part deterministically. Keeps the domain from
        the drafted value since that comes from config (COMPANY_NAME etc.)
        and is already correct. Falls back to the drafted value as-is if
        no name field is available or the email can't be parsed cleanly.
        """
        # Find the domain from the drafted email
        at_idx = drafted_email.rfind("@")
        if at_idx == -1:
            return drafted_email
        domain = drafted_email[at_idx + 1:].strip().lower()

        # Look for a name field in known_fields — try candidate first, then
        # contractor, then any key containing NAME.
        name = None
        for name_key in ("CANDIDATE_NAME", "CONTRACTOR_NAME"):
            if name_key in known_fields:
                name = str(known_fields[name_key])
                break
        if not name:
            for k, v in known_fields.items():
                if "NAME" in k and v and not self._looks_like_invented_placeholder(str(v)):
                    name = str(v)
                    break

        if not name:
            return drafted_email

        # Derive firstname.lastname from the name, lowercase, letters only
        parts = name.strip().split()
        if len(parts) >= 2:
            local = f"{parts[0]}.{parts[-1]}".lower()
            local = re.sub(r"[^a-z.]", "", local)
        else:
            local = re.sub(r"[^a-z]", "", parts[0].lower())

        if not local or not domain:
            return drafted_email
        return f"{local}@{domain}"

    def _looks_like_invented_placeholder(self, value: str) -> bool:
        """
        Detect generic boilerplate the model invented instead of either
        drafting real content or returning the literal "{{KEY}}" fallback
        as instructed (e.g. "Your Company Name", "Your Name", "Your Title",
        "Your Address", "TBD"). Rather than maintaining a list of every
        specific phrase a model might invent, this catches the general
        "Your <Capitalized Word(s)>" shape, since a legitimately drafted
        clause value (payment terms, IP ownership, notice periods, etc.)
        essentially never legitimately starts that way.
        """
        value = value.strip()
        if re.match(r"^Your\s+[A-Z][a-zA-Z]*(\s+[A-Z][a-zA-Z]*)*$", value):
            return True

        generic_fillers = {"tbd", "to be determined", "n/a", "not specified", "unknown", "[name]", "[title]"}
        if value.lower() in generic_fillers:
            return True

        return False

    def _trim_echoed_context(self, value: str, sentence: str, key: str) -> str:
        """
        Strip a leading or trailing fragment of `value` if it duplicates the
        text immediately before/after the {{KEY}} placeholder in `sentence`.
        Only trims on a real word-boundary match of at least a few words —
        short coincidental overlaps (e.g. a single shared word like "the")
        are left alone to avoid mangling legitimate values.
        """
        placeholder = f"{{{{{key}}}}}"
        if placeholder not in sentence:
            return value

        before, after = sentence.split(placeholder, 1)
        before = before.strip()
        # Strip trailing punctuation from `after` before tokenizing, so a
        # trailing period doesn't prevent "invoice." from matching "invoice"
        # in the drafted value.
        after = after.strip()
        trimmed = value.strip()

        # Trim a duplicated lead-in: if the value starts with the tail end
        # of the template's lead-in text, drop that overlap. The minimum
        # window of 1 allows catching single-word lead-ins like "Posted:"
        # or "Title:", guarded by a length check (4+ chars) to avoid false
        # positives on short common words like "the", "in", "a".
        before_words = before.split()
        min_n = 1 if (before_words and len(before_words[-1].strip(".,;:")) >= 4) else 2
        for n in range(min(len(before_words), 8), min_n - 1, -1):
            tail = " ".join(before_words[-n:])
            tail_clean = tail.strip(".,;:")
            if trimmed.lower().startswith(tail_clean.lower()):
                trimmed = trimmed[len(tail_clean):].strip()
                break

        # Trim a duplicated trailing text: if the value ends with the start
        # of the template's trailing text, drop that overlap. Compare with
        # trailing punctuation stripped from the template fragment, since
        # "invoice." in the template should still match "invoice" if a
        # drafted value echoes it without the period.
        after_words = after.split()
        min_n_after = 1 if (after_words and len(after_words[0].strip(".,;:")) >= 4) else 2
        for n in range(min(len(after_words), 8), min_n_after - 1, -1):
            head = " ".join(after_words[:n])
            head_clean = head.strip(".,;:")
            if trimmed.lower().endswith(head_clean.lower()):
                trimmed = trimmed[: len(trimmed) - len(head_clean)].strip()
                break
            if trimmed.lower().endswith(head.lower()):
                trimmed = trimmed[: len(trimmed) - len(head)].strip()
                break

        # If trimming removed the entire value, that means the original
        # value was made up entirely of text duplicating the template's own
        # surrounding wording — the correct result is an empty string, not
        # a fallback to the untrimmed original (which would silently
        # reintroduce the duplication this function exists to remove).
        return trimmed

    def _is_redundant_with_neighbor(self, value: str, sentence: str, key: str) -> bool:
        """
        Catch redundancy that word-boundary trimming can't, because the
        overlapping text is embedded inside a compound token rather than
        appearing as a separate word — e.g. a drafted RATE_TYPE value of
        "hour" when the immediately adjacent, already-filled RATE value is
        "$150/hour" (the word "hour" exists inside that token, not next to
        it as its own word). If the drafted value's core content already
        appears as a substring in the text immediately surrounding the
        blank, the value is redundant and should be dropped (the field
        will be set to an empty string by the caller, leaving the existing
        nearby text to carry the meaning on its own).
        """
        placeholder = f"{{{{{key}}}}}"
        if placeholder not in sentence:
            return False

        before, after = sentence.split(placeholder, 1)
        nearby = (before[-20:] + after[:20]).lower()
        core = value.strip().lower().lstrip("per ").strip()

        return bool(core) and core in nearby

    def _branch_style(self, index: int) -> str:
        """
        Return a distinct drafting style instruction for each branch index.
        Three styles ensure meaningful variation for the critic to evaluate.
        """
        styles = [
            "Formal and detailed — prioritize completeness and legal precision over brevity. Include all standard clauses.",
            "Clear and concise — prioritize plain language and readability for the candidate or contractor. Avoid jargon.",
            "Balanced — blend professional tone with warmth, appropriate for a small business context. Be thorough but approachable."
        ]
        return styles[index % len(styles)]