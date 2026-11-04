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
DOC_URL = "https://docs.google.com/document/d/{}/edit"

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class DocNotFound(Exception):
    """Document missing or not accessible under the granted Drive scope."""


class RevisionConflict(Exception):
    """requiredRevisionId did not match the document's current revision."""


class ExportTooLarge(Exception):
    """Export rejected: file exceeds Google's ~10MB export size limit."""


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

    def get_document_fresh(self, document_id: str, attempts: int = 4) -> dict:
        """Read a just-created/just-written doc, retrying briefly on 404.

        Google Docs occasionally returns 404/403 for a short window after
        creation or a large batchUpdate; those are transient, unlike a get
        on a genuinely foreign document id.
        """
        for attempt in range(attempts):
            try:
                return self.get_document(document_id)
            except DocNotFound:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.75 * (attempt + 1))

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
        # structure: insertTable only. Google places the table after the
        # paragraph containing the insertion index and gives every cell an
        # empty paragraph; pre-inserting newlines does NOT get absorbed into
        # cells and leaves stray empty paragraphs (verified empirically).
        self.batch_update(
            document_id,
            [{"insertTable": {"rows": rows, "columns": cols, "location": {"index": cursor}}}],
            required_revision_id,
        )
        # 2. read back for the actual table extent
        doc = self.get_document_fresh(document_id)
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
            requests.append(
                {
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": {
                                "tableStartLocation": {"index": table_start},
                                "rowIndex": 0,
                                "columnIndex": 0,
                            },
                            "rowSpan": 1,
                            "columnSpan": cols,
                        },
                        "tableCellStyle": {
                            "backgroundColor": {
                                "color": {"rgbColor": _rgb_from_hex(theme.table_header_bg)}
                            },
                        },
                        "fields": "backgroundColor",
                    }
                }
            )
        self.batch_update(document_id, requests, required_revision_id)
        # Cell fills grow the table (each cell gains a text unit), so re-read
        # for the real end index; the pre-fill value is stale.
        _, table_end = _find_table_at(self.get_document_fresh(document_id), table_start)
        return table_end

    # ---- drive helpers ----

    def search_files(
        self,
        *,
        name_query: str | None = None,
        full_text: str | None = None,
        folder_id: str | None = None,
        trashed: bool = False,
        mime: str = DOC_MIME_TYPE,
        limit: int = 10,
    ) -> list[dict]:
        clauses = []
        if name_query:
            escaped = name_query.replace("'", "\\'")
            clauses.append(f"name contains '{escaped}'")
        if full_text:
            escaped = full_text.replace("'", "\\'")
            clauses.append(f"fullText contains '{escaped}'")
        if folder_id:
            escaped = folder_id.replace("'", "\\'")
            clauses.append(f"'{escaped}' in parents")
        clauses.append(f"mimeType = '{mime}'")
        clauses.append("trashed = " + ("true" if trashed else "false"))
        q = " and ".join(clauses)
        response = _with_retries(
            lambda: self.drive.files()
            .list(q=q, pageSize=min(limit, 100), fields="files(id,name,modifiedTime,mimeType)")
            .execute()
        )
        return [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "url": DOC_URL.format(f["id"]),
                "modified_time": f.get("modifiedTime", ""),
            }
            for f in response.get("files", [])
        ]

    def search_documents(
        self,
        query: str,
        limit: int = 10,
        *,
        full_text: str | None = None,
        folder_id: str | None = None,
    ) -> list[dict]:
        return self.search_files(
            name_query=query or None, full_text=full_text, folder_id=folder_id, limit=limit
        )

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
        try:
            _with_retries(
                lambda: self.drive.files()
                .update(fileId=document_id, body={"trashed": True}, fields="id")
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise

    def rename_file(self, file_id: str, title: str) -> None:
        try:
            _with_retries(
                lambda: self.drive.files()
                .update(fileId=file_id, body={"name": title}, fields="id")
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise

    def move_file(self, file_id: str, add_parent_id: str, remove_current: bool = True) -> None:
        try:
            current = _with_retries(
                lambda: self.drive.files().get(fileId=file_id, fields="parents").execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        params: dict = {"fileId": file_id, "addParents": add_parent_id, "fields": "id"}
        if remove_current and current.get("parents"):
            params["removeParents"] = ",".join(current["parents"])
        try:
            _with_retries(lambda: self.drive.files().update(**params).execute())
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise

    def copy_document(self, file_id: str, new_title: str | None = None) -> dict:
        body = {"name": new_title} if new_title else {}
        try:
            result = _with_retries(
                lambda: self.drive.files().copy(fileId=file_id, body=body).execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        return {
            "id": result["id"],
            "name": result.get("name", ""),
            "url": DOC_URL.format(result["id"]),
        }

    def export_file(self, file_id: str, mime: str = "application/pdf") -> bytes:
        try:
            return _with_retries(
                lambda: self.drive.files().export(fileId=file_id, mimeType=mime).execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 403 and "exportSizeLimitExceeded" in str(exc):
                raise ExportTooLarge() from exc
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise

    def untrash_document(self, file_id: str) -> None:
        try:
            _with_retries(
                lambda: self.drive.files()
                .update(fileId=file_id, body={"trashed": False}, fields="id")
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise

    def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        body: dict = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            body["parents"] = [parent_id]
        try:
            result = _with_retries(lambda: self.drive.files().create(body=body).execute())
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        return {
            "id": result["id"],
            "name": result.get("name", ""),
            "parents": result.get("parents", []),
        }

    def list_folders(self, limit: int = 100) -> list[dict]:
        response = _with_retries(
            lambda: self.drive.files()
            .list(
                q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                pageSize=min(limit, 100),
                fields="files(id,name,parents)",
            )
            .execute()
        )
        return [
            {"id": f["id"], "name": f.get("name", ""), "parents": f.get("parents", [])}
            for f in response.get("files", [])
        ]

    def share_file(self, file_id: str, email: str, role: str, notify: bool = True) -> dict:
        # A 403 here means sharing is disabled for the domain/file; only 404
        # maps to DocNotFound.
        try:
            result = _with_retries(
                lambda: self.drive.permissions()
                .create(
                    fileId=file_id,
                    body={"type": "user", "role": role, "emailAddress": email},
                    sendNotificationEmail=notify,
                    fields="id",
                )
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        return {"id": result["id"], "email": email, "role": role}

    def list_permissions(self, file_id: str) -> list[dict]:
        try:
            response = _with_retries(
                lambda: self.drive.permissions()
                .list(fileId=file_id, fields="permissions(id,emailAddress,role,type)")
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        return [
            {
                "id": p["id"],
                "email": p.get("emailAddress", ""),
                "role": p.get("role", ""),
                "type": p.get("type", ""),
            }
            for p in response.get("permissions", [])
        ]

    def list_comments(self, file_id: str, limit: int = 50) -> list[dict]:
        try:
            response = _with_retries(
                lambda: self.drive.comments()
                .list(
                    fileId=file_id,
                    pageSize=min(limit, 100),
                    fields="comments(id,author(displayName),content,createdTime,resolved)",
                )
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        return [
            {
                "id": c["id"],
                "author": (c.get("author") or {}).get("displayName", ""),
                "content": c.get("content", ""),
                "created_time": c.get("createdTime", ""),
                "resolved": c.get("resolved", False),
            }
            for c in response.get("comments", [])
        ]

    def add_comment(self, file_id: str, content: str) -> dict:
        try:
            result = _with_retries(
                lambda: self.drive.comments()
                .create(fileId=file_id, body={"content": content}, fields="id")
                .execute()
            )
        except HttpError as exc:
            status = exc.resp.status if exc.resp is not None else None
            if status == 404:
                raise DocNotFound(str(exc)) from exc
            raise
        return {"id": result["id"], "content": result.get("content", "")}

    def find_by_exact_title(self, title: str) -> dict | None:
        escaped = title.replace("'", "\\'")
        q = f"name = '{escaped}' and mimeType = '{DOC_MIME_TYPE}' and trashed = false"
        response = _with_retries(
            lambda: self.drive.files()
            .list(
                q=q,
                pageSize=1,
                orderBy="modifiedTime desc",
                fields="files(id,name,modifiedTime,mimeType)",
            )
            .execute()
        )
        files = response.get("files", [])
        if not files:
            return None
        f = files[0]
        return {
            "id": f["id"],
            "name": f.get("name", ""),
            "url": DOC_URL.format(f["id"]),
            "modified_time": f.get("modifiedTime", ""),
        }


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


def _element_at(doc: dict, index: int) -> dict | None:
    """The top-level body element starting exactly at `index`, if any."""
    for element in doc.get("body", {}).get("content", []):
        if element.get("startIndex") == index:
            return element
    return None


def _find_table_at(doc: dict, index: int) -> tuple[int, int]:
    """First table element at or after `index`.

    Google places an inserted table after the paragraph containing the
    insertion index, so the table's startIndex is typically index+1, not an
    exact match.
    """
    for element in doc.get("body", {}).get("content", []):
        if "table" in element and element.get("startIndex", 0) >= index:
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
