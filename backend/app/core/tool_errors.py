"""Machine-readable errors for the agent tool API.

Every failure on /api/v1/tools/* carries a machine-readable `error` code so
the Grok bot can branch on it without parsing human messages.
"""

from typing import Any


class ToolApiError(Exception):
    def __init__(
        self, code: str, status_code: int, message: str, extra: dict[str, Any] | None = None
    ):
        self.code = code
        self.status_code = status_code
        self.message = message
        self.extra = extra or {}
        super().__init__(message)


def google_not_connected() -> ToolApiError:
    return ToolApiError(
        "google_not_connected",
        409,
        "No active Google connection for this API key's owner. Connect Google in the dashboard.",
    )


def google_reconnect_required() -> ToolApiError:
    return ToolApiError(
        "google_reconnect_required",
        403,
        "Google refresh token is expired or revoked (Testing-mode tokens expire after 7 days). "
        "Reconnect Google in the dashboard.",
    )


def google_api_error(status: int, message: str) -> ToolApiError:
    return ToolApiError("google_api_error", status, message)


def doc_not_found(doc_id: str) -> ToolApiError:
    return ToolApiError(
        "doc_not_found",
        404,
        f"Document {doc_id} not found or not accessible (drive.file scope: only "
        "app-created or Picker-granted docs are visible).",
    )


def spreadsheet_not_found(spreadsheet_id: str) -> ToolApiError:
    return ToolApiError(
        "spreadsheet_not_found",
        404,
        f"Spreadsheet {spreadsheet_id} not found or not accessible (drive.file scope: only "
        "app-created or Picker-granted spreadsheets are visible).",
    )


def sheet_not_found(name: str, available: list[str]) -> ToolApiError:
    return ToolApiError(
        "sheet_not_found", 404, f"sheet '{name}' not found; available: {', '.join(available)}"
    )


def sheet_too_large(cells: int) -> ToolApiError:
    return ToolApiError(
        "sheet_too_large",
        422,
        f"Read of {cells:,} cells exceeds the 200,000-cell cap; "
        "page reads with the range parameter.",
    )


def conflict_detected(current_revision_id: str) -> ToolApiError:
    return ToolApiError(
        "conflict_detected",
        409,
        "Document was modified since the revision you read. Re-read the document and retry.",
        {"current_revision_id": current_revision_id},
    )


def confirmation_required(preview: dict[str, Any]) -> ToolApiError:
    return ToolApiError(
        "confirmation_required",
        422,
        "This operation is destructive and requires confirm: true. Nothing was changed.",
        {"preview": preview},
    )


def invalid_request(message: str, extra: dict[str, Any] | None = None) -> ToolApiError:
    return ToolApiError("invalid_request", 422, message, extra)
