"""Genie Conversations API client.

Wraps the Databricks Genie Agent REST API for use in a Databricks App. Handles
authentication, polling, attachment parsing and result extraction.

Authentication comes from the Databricks SDK, which resolves credentials in this
order: environment variables (DATABRICKS_HOST, DATABRICKS_TOKEN) when running
locally, or the injected service principal when running as a Databricks App. No
credentials are handled directly here.

Usage:

    client = GenieClient(space_id="01f1a2dfca9c105eab89635b24ad21ae")
    turn = client.ask("Which councils have the most exposed network?")
    print(turn.text)
    print(turn.sql)
    print(turn.rows)

    follow_up = client.ask("Just the top three", conversation_id=turn.conversation_id)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from databricks.sdk import WorkspaceClient

log = logging.getLogger("bushfire.genie")


# --------------------------------------------------------------------------
# Status handling
# --------------------------------------------------------------------------

# Message statuses the API can return. Anything not terminal means keep polling.
TERMINAL_OK = {"COMPLETED"}
TERMINAL_FAIL = {"FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}

# Shown to the user while polling, so the wait feels like progress rather than
# a hang. Order roughly matches the sequence Genie moves through.
STATUS_LABELS = {
    "SUBMITTED": "Sending your question",
    "FETCHING_METADATA": "Reading table metadata",
    "FILTERING_CONTEXT": "Working out which tables are relevant",
    "ASKING_AI": "Thinking",
    "PENDING_WAREHOUSE": "Waiting for the SQL warehouse",
    "EXECUTING_QUERY": "Running the query",
    "COMPLETED": "Done",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
    "QUERY_RESULT_EXPIRED": "Result expired",
}


class GenieError(RuntimeError):
    """Genie returned a failure status or an unusable response."""


class GenieTimeout(GenieError):
    """Genie did not reach a terminal status within the timeout."""


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------


@dataclass
class GenieTurn:
    """One question and its answer."""

    conversation_id: str
    message_id: str
    question: str
    status: str

    text: Optional[str] = None
    """Genie's prose answer, if it produced one."""

    follow_up: Optional[str] = None
    """A clarifying question Genie asked back, if any. Distinct from `text`:
    a completed message can carry both, and conflating them makes Genie look
    like it dodged the question."""

    sql: Optional[str] = None
    """The SQL Genie generated. None where the answer needed no query."""

    sql_description: Optional[str] = None
    attachment_id: Optional[str] = None

    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    row_count: int = 0

    error: Optional[str] = None
    elapsed_seconds: float = 0.0

    @property
    def has_data(self) -> bool:
        return bool(self.columns and self.rows)

    @property
    def ok(self) -> bool:
        return self.status in TERMINAL_OK and not self.error


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class GenieClient:
    """Thin, defensive wrapper over the Genie Conversations API."""

    def __init__(
        self,
        space_id: str,
        workspace_client: Optional[WorkspaceClient] = None,
        poll_interval: float = 1.5,
        timeout_seconds: float = 180.0,
        max_rows: int = 500,
    ) -> None:
        if not space_id:
            raise ValueError("space_id is required")

        self.space_id = space_id
        self.poll_interval = poll_interval
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self._w = workspace_client or WorkspaceClient()

        log.info("GenieClient ready for space %s", space_id)

    # -- HTTP ------------------------------------------------------------

    def _do(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Call the API and always return a dict, never None."""
        log.debug("%s %s", method, path)
        try:
            response = self._w.api_client.do(method, path, body=body)
        except Exception as exc:  # noqa: BLE001 - surface the real cause
            log.exception("API call failed: %s %s", method, path)
            raise GenieError(f"Databricks API call failed: {exc}") from exc

        return response if isinstance(response, dict) else {}

    # -- Public ----------------------------------------------------------

    def ask(
        self,
        question: str,
        conversation_id: Optional[str] = None,
        on_status: Optional[Callable[[str, str], None]] = None,
    ) -> GenieTurn:
        """Ask a question and wait for the answer.

        Passing conversation_id continues an existing thread, which is what
        makes follow-ups like "just the top three" work.

        on_status is called with (raw_status, friendly_label) on every poll,
        so the UI can show progress instead of a spinner.
        """
        question = (question or "").strip()
        if not question:
            raise ValueError("question must not be empty")

        started = time.monotonic()

        if conversation_id:
            log.info("Follow-up in conversation %s: %s", conversation_id, question[:80])
            path = (
                f"/api/2.0/genie/spaces/{self.space_id}"
                f"/conversations/{conversation_id}/messages"
            )
            payload = self._do("POST", path, {"content": question})
            message = payload if "id" in payload else payload.get("message", {})
            new_conversation_id = conversation_id
        else:
            log.info("New conversation: %s", question[:80])
            path = f"/api/2.0/genie/spaces/{self.space_id}/start-conversation"
            payload = self._do("POST", path, {"content": question})
            message = payload.get("message", {})
            new_conversation_id = payload.get("conversation", {}).get("id")

        message_id = message.get("id") or message.get("message_id")

        if not (new_conversation_id and message_id):
            raise GenieError(
                "Genie did not return a conversation and message id. "
                f"Response keys: {sorted(payload.keys())}"
            )

        final = self._poll(new_conversation_id, message_id, on_status)
        turn = self._build_turn(
            new_conversation_id, message_id, question, final
        )
        turn.elapsed_seconds = round(time.monotonic() - started, 1)

        log.info(
            "Turn complete in %.1fs: status=%s sql=%s rows=%d",
            turn.elapsed_seconds,
            turn.status,
            bool(turn.sql),
            turn.row_count,
        )
        return turn

    # -- Polling ---------------------------------------------------------

    def _poll(
        self,
        conversation_id: str,
        message_id: str,
        on_status: Optional[Callable[[str, str], None]],
    ) -> dict:
        path = (
            f"/api/2.0/genie/spaces/{self.space_id}"
            f"/conversations/{conversation_id}/messages/{message_id}"
        )
        deadline = time.monotonic() + self.timeout_seconds
        last_status = None

        while time.monotonic() < deadline:
            payload = self._do("GET", path)
            status = payload.get("status", "UNKNOWN")

            if status != last_status:
                label = STATUS_LABELS.get(status, status.replace("_", " ").title())
                log.debug("Status: %s", status)
                if on_status:
                    on_status(status, label)
                last_status = status

            if status in TERMINAL_OK or status in TERMINAL_FAIL:
                return payload

            time.sleep(self.poll_interval)

        raise GenieTimeout(
            f"Genie did not finish within {self.timeout_seconds:.0f}s "
            f"(last status: {last_status})"
        )

    # -- Parsing ---------------------------------------------------------

    def _build_turn(
        self,
        conversation_id: str,
        message_id: str,
        question: str,
        payload: dict,
    ) -> GenieTurn:
        status = payload.get("status", "UNKNOWN")
        turn = GenieTurn(
            conversation_id=conversation_id,
            message_id=message_id,
            question=question,
            status=status,
        )

        if status in TERMINAL_FAIL:
            err = payload.get("error") or {}
            turn.error = err.get("error") or err.get("message") or status
            log.warning("Genie failed: %s", turn.error)
            return turn

        for attachment in payload.get("attachments") or []:
            self._read_attachment(attachment, turn)

        if turn.attachment_id:
            try:
                self._fetch_result(turn)
            except GenieError as exc:
                # A missing result is not fatal. The prose answer is still
                # worth showing, so degrade rather than fail the whole turn.
                log.warning("Could not fetch query result: %s", exc)
                turn.error = f"Query result unavailable: {exc}"

        if not turn.text and not turn.follow_up and not turn.has_data:
            turn.error = turn.error or "Genie returned an empty response."

        return turn

    @staticmethod
    def _read_attachment(attachment: dict, turn: GenieTurn) -> None:
        """Pull text and query out of one attachment.

        A completed message can carry several attachments, including both a
        clarifying question and a final answer. The purpose field separates
        them; treating a follow-up as the answer makes Genie look evasive.
        """
        text_part = attachment.get("text")
        if isinstance(text_part, dict):
            content = text_part.get("content")
            purpose = text_part.get("purpose", "")
            if content:
                if purpose == "FOLLOW_UP_QUESTION":
                    turn.follow_up = content
                else:
                    turn.text = content

        query_part = attachment.get("query")
        if isinstance(query_part, dict):
            turn.sql = query_part.get("query") or turn.sql
            turn.sql_description = (
                query_part.get("description") or turn.sql_description
            )
            turn.attachment_id = attachment.get("attachment_id") or turn.attachment_id

    def _fetch_result(self, turn: GenieTurn) -> None:
        path = (
            f"/api/2.0/genie/spaces/{self.space_id}"
            f"/conversations/{turn.conversation_id}"
            f"/messages/{turn.message_id}"
            f"/attachments/{turn.attachment_id}/query-result"
        )
        payload = self._do("GET", path)
        statement = payload.get("statement_response") or {}

        manifest = statement.get("manifest") or {}
        schema = manifest.get("schema") or {}
        turn.columns = [c.get("name", f"col_{i}") for i, c in enumerate(schema.get("columns") or [])]
        turn.truncated = bool(manifest.get("truncated"))

        result = statement.get("result") or {}
        turn.rows = self._extract_rows(result, self.max_rows)
        turn.row_count = manifest.get("total_row_count") or len(turn.rows)

        log.debug("Fetched %d rows, %d columns", len(turn.rows), len(turn.columns))

    @staticmethod
    def _extract_rows(result: dict, max_rows: int) -> list[list[Any]]:
        """Handle both result shapes the API returns.

        `data_array` is documented. `data_typed_array` is what the API often
        actually sends, with each value wrapped as {"str": "..."}. Code that
        handles only the documented shape silently returns no rows.
        """
        if result.get("data_array"):
            return [list(r) for r in result["data_array"][:max_rows]]

        typed = result.get("data_typed_array") or []
        rows: list[list[Any]] = []
        for entry in typed[:max_rows]:
            values = entry.get("values") or []
            rows.append([v.get("str") if isinstance(v, dict) else v for v in values])
        return rows


# --------------------------------------------------------------------------
# Convenience
# --------------------------------------------------------------------------


def rows_to_records(turn: GenieTurn) -> list[dict[str, Any]]:
    """Turn columns plus rows into dicts, for DataFrame construction."""
    return [dict(zip(turn.columns, row)) for row in turn.rows]
