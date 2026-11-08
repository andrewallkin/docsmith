"""Shared Google Drive file operations and provider exceptions."""

import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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


class DriveFileOps:
    def __init__(self, credentials: Credentials):
        self._credentials = credentials
        self._docs = None
        self._drive = None

    @property
    def drive(self):
        if self._drive is None:
            self._drive = build("drive", "v3", credentials=self._credentials, cache_discovery=False)
        return self._drive

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
