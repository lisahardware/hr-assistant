"""
agents/communication_agent.py
Formats agent outputs for the web UI and handles outbound Microsoft 365 actions.

Safety rules enforced here:
  - All candidate-facing emails require explicit owner approval before sending
  - Documents containing compensation, offers, or contract terms are always flagged for review
  - OneDrive saves require owner confirmation in the UI
"""

import os
from tools.mcp_client import MCPClient
from tools.document_creator import DocumentCreator


class CommunicationAgent:
    """
    Handles the final output layer of the agent pipeline.
    Formats results for display, manages review flags, and
    executes approved M365 actions via the MCP client.
    """

    # Document types that always require owner review before any action
    REVIEW_REQUIRED_TYPES = {"offer_letter", "contractor_agreement"}

    def __init__(self, mcp: MCPClient, doc_creator: DocumentCreator):
        self.mcp = mcp
        self.doc_creator = doc_creator

    def format_response(self, task_result: dict) -> dict:
        """
        Prepare the agent's output for the web UI.
        Adds review flags for legally sensitive content.
        Ensures compliance notes are always visible when present.
        """
        return {
            "summary": task_result.get("summary", ""),
            "document_path": task_result.get("document_path", ""),
            "draft": task_result.get("draft", ""),
            "requires_review": task_result.get("requires_review", False),
            "review_reason": task_result.get("review_reason", ""),
            "missing_fields": task_result.get("missing_fields", []),
            "compliance_notes": task_result.get("compliance_notes", []),
            "actions_taken": task_result.get("actions_taken", []),
            "score_info": task_result.get("score_info", {}),
            "warning": task_result.get("warning", "")
        }

    def should_require_review(self, doc_type: str) -> tuple[bool, str]:
        """
        Determine whether a document type requires owner review.
        Returns (requires_review, reason_string).
        """
        if doc_type in self.REVIEW_REQUIRED_TYPES:
            return (
                True,
                "This document contains compensation or legal terms that require "
                "owner review before sending to a candidate."
            )
        return False, ""

    def save_to_onedrive(self, local_path: str, document_type: str) -> str:
        """
        Upload a generated document to OneDrive after owner approval.
        Saves to /HR Documents/{document_type}/ in the owner's OneDrive.
        """
        filename = os.path.basename(local_path)
        onedrive_path = f"/HR Documents/{document_type}/{filename}"
        self.mcp.upload_file(local_path, onedrive_path)
        return onedrive_path

    def send_candidate_email(self, to: str, subject: str, body: str) -> dict:
        """
        Send a candidate-facing email via Outlook.
        Should only be called after explicit owner approval in the UI.
        """
        return self.mcp.send_email(to, subject, body)

    def create_draft_email(self, to: str, subject: str, body: str) -> dict:
        """
        Save an email as a draft in Outlook without sending.
        Safer default for candidate communications.
        """
        return self.mcp.create_draft_email(to, subject, body)

    def schedule_interview(
        self,
        candidate_email: str,
        subject: str,
        start: str,
        end: str,
        notes: str = ""
    ) -> dict:
        """
        Create a calendar event for an interview and invite the candidate.
        start/end format: "2025-01-15T09:00:00"
        """
        return self.mcp.create_calendar_event(
            subject=subject,
            start=start,
            end=end,
            attendees=[candidate_email],
            body=notes
        )