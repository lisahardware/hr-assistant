"""
tools/mcp_client.py
Connects the Python agent layer to the Microsoft 365 MCP server
running in Docker on localhost:3000.
All Microsoft 365 actions (email, calendar, OneDrive) go through here.

IMPORTANT: this server's actual tool names and parameter shapes were
discovered directly via a tools/list call against the running server —
they do not match the tool names/shapes this file originally assumed
(e.g. "mail-send-message" does not exist; the real tool is "send-mail"
with a very different nested body shape). See the docstring on each
method for the exact schema it's built against.
"""

import os
import base64
import httpx
import json
from typing import Optional


class MCPClient:
    def __init__(self, base_url: str = None):
        base_url = base_url or os.environ.get("MCP_SERVER_URL", "http://localhost:3000")
        self.base_url = base_url
        self.mcp_url = f"{base_url}/mcp"
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _call(self, tool_name: str, arguments: dict) -> dict:
        """
        Make a synchronous JSON-RPC call to the MCP server.
        Returns the result dict or raises on error.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            },
            "id": self._next_id()
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                # The MCP Streamable HTTP transport spec requires clients to
                # declare they accept both plain JSON and SSE responses —
                # without this header, the server can return 406 Not
                # Acceptable even though the request itself is otherwise
                # valid and authenticated correctly.
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                }
                response = client.post(self.mcp_url, json=payload, headers=headers)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    # SSE response: parse out the JSON payload from the
                    # last "data:" line in the event stream. This server
                    # consistently responds with text/event-stream for
                    # tools/call and tools/list, even for single-shot
                    # (non-streamed) results.
                    data = None
                    for line in response.text.splitlines():
                        if line.startswith("data:"):
                            data = json.loads(line[len("data:"):].strip())
                    if data is None:
                        raise Exception(
                            f"MCP server returned an SSE response with no parseable "
                            f"data payload: {response.text[:500]}"
                        )
                else:
                    data = response.json()

            if "error" in data:
                raise Exception(f"MCP error: {data['error']}")

            result = data.get("result", {})

            # MCP tool-call failures (wrong tool name, invalid params, the
            # underlying Graph API call failing, etc.) come back wrapped
            # INSIDE the result payload as {"isError": true, "content": [...]},
            # not as a top-level JSON-RPC "error" key. Without this check,
            # a failed tool call is silently treated as success.
            if isinstance(result, dict) and result.get("isError"):
                error_text = ""
                for block in result.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        error_text += block.get("text", "")
                raise Exception(f"MCP tool '{tool_name}' failed: {error_text or result}")

            return result

        except httpx.ConnectError:
            raise Exception(
                f"Cannot connect to MCP server at {self.base_url}. "
                "Make sure the Docker container is running: docker start hr-mcp"
            )

    def _graph_batch(self, requests: list[dict]) -> dict:
        """
        Make one or more raw Microsoft Graph API calls via the graph-batch
        tool. Each request dict needs: id, method, url (relative to the
        Graph version root, e.g. "/me/drive/root:/HR Documents:/children"),
        and optionally headers/body. Returns the raw graph-batch result;
        responses are in arbitrary order, matched by id.

        IMPORTANT: graph-batch's own input schema requires a top-level
        "body" object parameter, which itself contains "requests" — i.e.
        the call shape is {"body": {"requests": [...]}}, NOT
        {"requests": [...]} at the top level. Easy to miss since the
        tool's prose description says 'Body: { requests: [...] }' which
        reads ambiguously — "Body" there refers to the schema's "body"
        field name, not a generic description of the call shape.
        """
        return self._call("graph-batch", {"body": {"requests": requests}})

    def is_available(self) -> bool:
        """Check if the MCP server is reachable."""
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/")
                return response.status_code < 500
        except Exception:
            return False

    # -------------------------
    # Email (Outlook)
    # -------------------------

    def send_email(self, to: str, subject: str, body: str) -> dict:
        """
        Send an email via Outlook using the real "send-mail" tool schema:
        body.Message.{subject, body.content/contentType, toRecipients},
        plus body.SaveToSentItems.
        """
        return self._call("send-mail", {
            "body": {
                "Message": {
                    "subject": subject,
                    "body": {"contentType": "text", "content": body},
                    "toRecipients": [
                        {"emailAddress": {"address": to}}
                    ]
                },
                "SaveToSentItems": True
            }
        })

    def list_emails(self, folder: str = "inbox", top: int = 10) -> list[dict]:
        """List recent emails from a folder."""
        result = self._call("list-mail-messages", {
            "top": top
        })
        return result.get("value", [])

    def get_email(self, message_id: str) -> dict:
        """Get a specific email by ID."""
        return self._call("get-mail-message", {"messageId": message_id})

    def create_draft_email(self, to: str, subject: str, body: str) -> dict:
        """
        Create a draft email without sending, using the real
        "create-draft-email" tool schema: body.{subject, body.content/
        contentType, toRecipients} — note this tool's body is NOT wrapped
        in an extra "Message" key the way send-mail's is.
        """
        return self._call("create-draft-email", {
            "body": {
                "subject": subject,
                "body": {"contentType": "text", "content": body},
                "toRecipients": [
                    {"emailAddress": {"address": to}}
                ]
            }
        })

    # -------------------------
    # Calendar
    # -------------------------

    def list_calendar_events(self, start: str, end: str) -> list[dict]:
        """
        List calendar events in a date range.
        start/end format: "2025-01-15T09:00:00"
        """
        result = self._call("list-calendar-events", {
            "startDateTime": start,
            "endDateTime": end
        })
        return result.get("value", [])

    def create_calendar_event(
        self,
        subject: str,
        start: str,
        end: str,
        attendees: list[str],
        body: str = ""
    ) -> dict:
        """
        Create a calendar event and invite attendees, using the real
        "create-calendar-event" tool schema: body.{subject, start.dateTime/
        timeZone, end.dateTime/timeZone, attendees[].emailAddress.address,
        body.content/contentType}.
        start/end format: "2025-01-15T09:00:00"
        attendees: list of email addresses
        """
        return self._call("create-calendar-event", {
            "body": {
                "subject": subject,
                "start": {"dateTime": start, "timeZone": "Eastern Standard Time"},
                "end": {"dateTime": end, "timeZone": "Eastern Standard Time"},
                "attendees": [
                    {"emailAddress": {"address": a}, "type": "required"}
                    for a in attendees
                ],
                "body": {"contentType": "text", "content": body}
            }
        })

    # -------------------------
    # OneDrive
    # -------------------------

    def upload_file(self, local_path: str, onedrive_path: str) -> dict:
        """
        Upload a local file to OneDrive at an arbitrary path, creating it
        if it doesn't already exist.

        NOTE: the named "upload-file-content" tool requires an existing
        driveId + driveItemId (i.e. it can only overwrite a file that
        already exists and whose item ID you already know) — it cannot
        create a brand-new file at a path string like "/HR Documents/
        Contracts/file.docx". For "create at this path" semantics, we use
        graph-batch to call Microsoft Graph's path-addressed upload
        endpoint directly:
            PUT /me/drive/root:/{path}:/content
        which creates the file (and its content) in one call, using
        Graph's path-based addressing rather than requiring a pre-resolved
        item ID.

        onedrive_path example: "/HR Documents/Offer Letters/filename.docx"
        """
        with open(local_path, "rb") as f:
            content = f.read()

        # Graph's path-addressed endpoints expect the path WITHOUT a
        # leading slash inside the root:/.../:  segment.
        graph_path = onedrive_path.lstrip("/")

        result = self._graph_batch([
            {
                "id": "1",
                "method": "PUT",
                "url": f"/me/drive/root:/{graph_path}:/content",
                # graph-batch sends this as the request body to Graph;
                # binary content for a batched PUT needs to be base64
                # encoded — Graph's batch endpoint decodes it back to
                # raw bytes before writing. Tested as a plain base64
                # string first (not wrapped in an object) since that
                # matches typical raw-content PUT semantics; if the
                # server rejects this shape, the next thing to try is
                # wrapping it as {"content": "<base64>"} or similar.
                "body": base64.b64encode(content).decode("ascii"),
                "headers": {"Content-Type": "application/octet-stream"}
            }
        ])

        return result

    def list_onedrive_files(self, folder_path: str = "/HR Documents") -> list[dict]:
        """
        List files in a OneDrive folder via graph-batch, since the named
        "list-folder-files" tool requires a driveId + driveItemId (item
        IDs we don't have for an arbitrary path) rather than accepting a
        path string directly.
        """
        graph_path = folder_path.strip("/")
        url = f"/me/drive/root:/{graph_path}:/children" if graph_path else "/me/drive/root/children"

        result = self._graph_batch([
            {"id": "1", "method": "GET", "url": url}
        ])
        return result

    def download_file(self, onedrive_path: str, local_path: str) -> str:
        """
        Download a file from OneDrive to a local path via graph-batch,
        using Graph's path-addressed content endpoint.

        NOTE: this method has not yet been exercised against the live
        server — graph-batch's exact handling of binary response bodies
        for a GET .../content call (raw bytes vs. base64-wrapped JSON) is
        unconfirmed. If this is used, verify the response shape with a
        small test file before relying on it, the same way we had to
        verify upload_file's actual behavior rather than assume it.
        """
        graph_path = onedrive_path.lstrip("/")
        result = self._graph_batch([
            {"id": "1", "method": "GET", "url": f"/me/drive/root:/{graph_path}:/content"}
        ])
        return result