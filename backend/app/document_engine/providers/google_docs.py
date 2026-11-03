"""Google Docs / Drive provider — the only Google-coupled module in the engine.

Executes render plans against the Google Docs API, performs searches and
trash operations via the Drive API, and maps Google errors onto service
exceptions. Transient failures are retried with exponential backoff (max 3),
except 4xx.
"""

import logging
import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.document_engine.renderer import BatchStep, TableStep, _table_cell_insert_index, utf16_len
from app.document_engine.themes import Theme

logger = logging.getLogger(__name__)

DOC_MIME_TYPE = "application/vnd.google-apps.document"

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class DocNotFound(Exception):
    """Document missing or not accessible under drive.file scope."""


class RevisionConflict(Exception):
    """requiredRevisionId did not match the document's current revision."""


def _backoff_statuses(exc: HttpError) -> bool:
    status = exc.resp.status if exc.resp is not None else None
    return status in RETRYABLE_STATUS


def _with_retries(call):
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call()
        except HttpError as exc:
            if not _backoff_statuses(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(2**attempt * 0.5)
        except ConnectionError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(2**attempt * 0.5)


class GoogleDocsProvider:
    def __init__(self, credentials: Credentials):
        self._credentials = credentials
        self._docs = None
        self._drive = None

    @property
    def docs(self):
        if self._docs is None:
            self._docs = build("docs", "v1", credentials=self._credentials, cache_discovery=False)
        return self._docs

    @property
    def drive(self):
        if self._drive is None:
            self._drive = build("drive", "v3", credentials=self._credentials, cache_discovery=False)
        return self._drive

    # ---- document basics ----

    def create_document(self, title: str) -> dict:
        return _with_retries(lambda: self.docs.documents().create(body={"title": title}).execute())

    def get_document(self, document_id: str) -> dict:
        try:
            return _with_retries(
                lambda: self.docs.documents().get(documentId=document_id).execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status in (403, 404):
                raise DocNotFound() from exc
            raise

    def batch_update(
        self, document_id: str, requests: list[dict], required_revision_id: str | None = None
    ) -> dict:
        body: dict = {"requests": requests}
        if required_revision_id:
            body["writeControl"] = {"requiredRevisionId": required_revision_id}
        try:
            return _with_retries(
                lambda: self.docs.documents()
                .batchUpdate(documentId=document_id, body=body)
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 412 or "required revision" in str(exc).lower():
                raise RevisionConflict() from exc
            raise

    # ---- plan execution ----

    def execute_plan(
        self,
        document_id: str,
        steps: list,
        start_cursor: int,
        theme: "Theme",
        required_revision_id: str | None = None,
    ) -> tuple[int, str]:
        """Execute render steps; returns (final_cursor, applied_revision_id).

        start_cursor: actual document index matching the first step's virtual
        start. applied_revision_id: the revision the writes were applied
        against (WriteControl).
        """
        virtual = steps[0].start_cursor if steps else start_cursor
        actual = start_cursor
        applied_revision = required_revision_id
        for step in steps:
            shift = actual - virtual
            if isinstance(step, BatchStep):
                shifted = [_shift_request(request, shift) for request in step.requests]
                response = self.batch_update(document_id, shifted, required_revision_id)
                applied_revision = (
                    (response.get("writeControl") or {}).get("requiredRevisionId")
                    or applied_revision
                )
                virtual = step.cursor
                actual += step.cursor - step.start_cursor
            elif isinstance(step, TableStep):
                actual = self._execute_table_step(
                    document_id, step, actual, theme, required_revision_id
                )
                virtual = step.cursor
        return actual, applied_revision or ""

    def _execute_table_step(
        self,
        document_id: str,
        step: TableStep,
        cursor: int,
        theme: "Theme",
        required_revision_id: str | None,
    ) -> int:
        rows, cols = step.rows, step.cols
        # 1. structure: newlines (claimed as cell paragraphs) + insertTable
        self.batch_update(
            document_id,
            [
                {"insertText": {"location": {"index": cursor}, "text": "\n" * rows}},
                {"insertTable": {"rows": rows, "columns": cols, "location": {"index": cursor}}},
            ],
            required_revision_id,
        )
        # 2. read back for the actual table extent
        doc = self.get_document(document_id)
        table_start, table_end = _find_table_at(doc, cursor)
        # 3. fill cells + styles (indices relative to table_start, with a
        #    running shift as earlier cells are filled)
        requests: list[dict] = []
        shift = 0
        for r, cell_runs in enumerate(_all_cell_runs(step)):
            cell_index = _table_cell_insert_index(table_start, r // cols, r % cols, cols)
            text = "".join(run.content for run in cell_runs)
            requests.append(
                {"insertText": {"location": {"index": cell_index + shift}, "text": text}}
            )
            length = utf16_len(text)
            if r == 0 and step.header:
                # header: bold, theme colour; background handled below
                requests.append(
                    {
                        "updateTextStyle": {
                            "range": {
                                "startIndex": cell_index + shift,
                                "endIndex": cell_index + shift + length,
                            },
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    }
                )
            shift += length
        if step.header:
            for c in range(cols):
                requests.append(
                    {
                        "updateTableCellStyle": {
                            "tableCellLocation": {
                                "tableStartLocation": {"index": table_start},
                                "rowIndex": 0,
                                "columnIndex": c,
                            },
                            "tableCellCellStyle": {
                                "backgroundColor": {
                                    "color": {"rgbColor": _rgb_from_hex(theme.table_header_bg)}
                                },
                            },
                            "fields": "backgroundColor",
                        }
                    }
                )
        self.batch_update(document_id, requests, required_revision_id)
        return table_end

    # ---- drive helpers ----

    def search_documents(self, query: str, limit: int = 10) -> list[dict]:
        escaped = query.replace("'", "\\'")
        q = f"name contains '{escaped}' and mimeType = '{DOC_MIME_TYPE}' and trashed = false"
        response = _with_retries(
            lambda: self.drive.files()
            .list(q=q, pageSize=min(limit, 100), fields="files(id,name,modifiedTime,mimeType)")
            .execute()
        )
        return [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "url": f"https://docs.google.com/document/d/{f['id']}/edit",
                "modified_time": f.get("modifiedTime", ""),
            }
            for f in response.get("files", [])
        ]

    def list_accessible_documents(self, limit: int = 20) -> list[dict]:
        response = _with_retries(
            lambda: self.drive.files()
            .list(
                q=f"mimeType = '{DOC_MIME_TYPE}' and trashed = false",
                pageSize=min(limit, 100),
                fields="files(id,name,modifiedTime,mimeType)",
            )
            .execute()
        )
        return [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "url": f"https://docs.google.com/document/d/{f['id']}/edit",
                "modified_time": f.get("modifiedTime", ""),
            }
            for f in response.get("files", [])
        ]

    def trash_document(self, document_id: str) -> None:
        _with_retries(
            lambda: self.drive.files()
            .update(fileId=document_id, body={"trashed": True}, fields="id")
            .execute()
        )


def _rgb_from_hex(hex_colour: str) -> dict:
    value = hex_colour.lstrip("#")
    return {
        "red": int(value[0:2], 16) / 255.0,
        "green": int(value[2:4], 16) / 255.0,
        "blue": int(value[4:6], 16) / 255.0,
    }


def _all_cell_runs(step: TableStep):
    """All cells in fill order: header row (if any) then body rows."""
    if step.header:
        yield from step.header
    for row in step.body_rows:
        yield from row


def _find_table_at(doc: dict, index: int) -> tuple[int, int]:
    for element in doc.get("body", {}).get("content", []):
        table = element.get("table")
        if table and element.get("startIndex") == index:
            return element["startIndex"], element["endIndex"]
    raise DocNotFound()


def _shift_request(request: dict, shift: int) -> dict:
    if shift == 0:
        return request
    import copy

    shifted = copy.deepcopy(request)
    for key in ("insertText", "insertPageBreak"):
        if key in shifted:
            shifted[key]["location"]["index"] += shift
    if "updateParagraphStyle" in shifted:
        rng = shifted["updateParagraphStyle"]["range"]
        rng["startIndex"] += shift
        rng["endIndex"] += shift
    if "updateTextStyle" in shifted:
        rng = shifted["updateTextStyle"]["range"]
        rng["startIndex"] += shift
        rng["endIndex"] += shift
    if "createParagraphBullets" in shifted:
        rng = shifted["createParagraphBullets"]["range"]
        rng["startIndex"] += shift
        rng["endIndex"] += shift
    if "deleteContentRange" in shifted:
        rng = shifted["deleteContentRange"]["range"]
        rng["startIndex"] += shift
        rng["endIndex"] += shift
    return shifted
