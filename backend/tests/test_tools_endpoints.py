"""Tool endpoints with a mocked Google provider (request/response contract)."""

import base64

import pytest

from app.services import document_service

SAMPLE_DOC_JSON = {
    "documentId": "doc123",
    "title": "Sample",
    "revisionId": "rev-1",
    "body": {
        "content": [
            {"sectionBreak": {}},
            {
                "startIndex": 1,
                "endIndex": 9,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "elements": [{"textRun": {"content": "Intro\n", "textStyle": {}}}],
                },
            },
            {
                "startIndex": 9,
                "endIndex": 30,
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "First paragraph\n", "textStyle": {}}}
                    ]
                },
            },
            {
                "startIndex": 30,
                "endIndex": 55,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "elements": [{"textRun": {"content": "Details\n", "textStyle": {}}}],
                },
            },
            {
                "startIndex": 55,
                "endIndex": 75,
                "paragraph": {
                    "elements": [{"textRun": {"content": "Details body\n", "textStyle": {}}}]
                },
            },
        ]
    },
}


class FakeProvider:
    """Records calls; returns canned documents."""

    def __init__(self):
        self.docs = {"doc123": dict(SAMPLE_DOC_JSON)}
        self.revision = "rev-1"
        self.created: list[dict] = []
        self.batches: list[list[dict]] = []
        self.trashed: list[str] = []
        self.untrashed: list[str] = []
        self.plan_calls: list[dict] = []
        self.calls: list[dict] = []
        self.existing_by_title = None
        self.create_document_calls: list[str] = []
        self.renamed: tuple[str, str] | None = None
        self.folders: list[dict] = []
        self.moves: list[tuple[str, str, bool]] = []
        self.export_bytes = b""
        self.copied: list[tuple[str, str | None]] = []
        self.permissions: list[dict] = []
        self.shared: list[tuple[str, str, str, bool]] = []

    def create_document(self, title):
        self.create_document_calls.append(title)
        return {"documentId": "new1", "title": title, "revisionId": self.revision}

    def find_by_exact_title(self, title):
        return self.existing_by_title

    def get_document(self, document_id):
        if document_id == "missing":
            from app.document_engine.providers.google_docs import DocNotFound

            raise DocNotFound()
        doc = self.docs.get(document_id, dict(SAMPLE_DOC_JSON))
        doc = dict(doc)
        doc["revisionId"] = self.revision
        return doc

    def get_document_fresh(self, document_id, attempts: int = 4):
        return self.get_document(document_id)

    def batch_update(self, document_id, requests, required_revision_id=None):
        self.batches.append([dict(r) for r in requests])
        self.revision = "rev-after-write"
        return {"writeControl": {"requiredRevisionId": self.revision}}

    def execute_plan(self, document_id, steps, start_cursor, theme, required_revision_id=None):
        self.plan_calls.append(
            {"document_id": document_id, "steps": steps, "start_cursor": start_cursor}
        )
        self.revision = "rev-after-write"
        return start_cursor, self.revision

    def search_documents(self, query, limit=10, full_text=None, folder_id=None):
        self.calls.append(
            {"query": query, "limit": limit, "full_text": full_text, "folder_id": folder_id}
        )
        return [
            {
                "id": "doc123",
                "name": "Sample",
                "url": "https://docs.google.com/document/d/doc123/edit",
                "modified_time": "2026-01-01T00:00:00Z",
            }
        ]

    def trash_document(self, document_id):
        from app.document_engine.providers.google_docs import DocNotFound

        if document_id == "missing":
            raise DocNotFound()
        self.trashed.append(document_id)

    def untrash_document(self, document_id):
        from app.document_engine.providers.google_docs import DocNotFound

        if document_id == "missing":
            raise DocNotFound()
        self.untrashed.append(document_id)

    def rename_file(self, file_id, title):
        self.renamed = (file_id, title)

    def create_folder(self, name, parent_id=None):
        folder = {
            "id": f"fld{len(self.folders) + 1}",
            "name": name,
            "parents": [parent_id] if parent_id else [],
        }
        self.folders.append(folder)
        return folder

    def list_folders(self, limit=100):
        return self.folders

    def move_file(self, file_id, add_parent_id, remove_current=True):
        self.moves.append((file_id, add_parent_id, remove_current))

    def copy_document(self, file_id, new_title=None):
        self.copied.append((file_id, new_title))
        return {
            "id": "copied1",
            "name": new_title or "Sample",
            "url": "https://docs.google.com/document/d/copied1/edit",
        }

    def export_file(self, file_id, mime="application/pdf"):
        return self.export_bytes

    def share_file(self, file_id, email, role, notify=True):
        self.shared.append((file_id, email, role, notify))
        return {"id": "perm1", "email": email, "role": role}

    def list_permissions(self, file_id):
        return self.permissions


@pytest.fixture()
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(document_service, "_provider", lambda db, ctx: provider)
    return provider


def test_search_endpoint(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "samp", "limit": 5},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    docs = res.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["id"] == "doc123"
    assert docs[0]["url"].endswith("/doc123/edit")


def test_search_empty_query_lists_all(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": ""},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200


def test_search_full_text(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/search",
        json={"query": "", "full_text": "kyoto hotel", "folder_id": "fld1"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.calls[-1]["full_text"] == "kyoto hotel"
    assert fake.calls[-1]["folder_id"] == "fld1"


def test_read_document(client, active_key, fake):
    res = client.get(
        "/api/v1/tools/documents/doc123", headers={"Authorization": f"Bearer {active_key}"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "doc123"
    assert body["title"] == "Sample"
    assert body["revision_id"] == "rev-1"
    assert "# Intro" in body["markdown"]
    assert "First paragraph" in body["markdown"]
    outline = body["outline"]
    assert [o["heading"] for o in outline] == ["Intro", "Details"]


def test_read_document_not_found(client, active_key, fake):
    res = client.get(
        "/api/v1/tools/documents/missing", headers={"Authorization": f"Bearer {active_key}"}
    )
    assert res.status_code == 404
    assert res.json()["error"] == "doc_not_found"


def test_create_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents",
        json={
            "title": "Weekly report",
            "markdown": "# Weekly report\n\nAll good.",
            "theme": "professional",
        },
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["document_id"] == "new1"
    assert body["document_url"].endswith("/new1/edit")
    assert body["summary"].startswith("Created document 'Weekly report'")
    # document style + plan executed with cursor starting at 1
    assert any("updateDocumentStyle" in r for r in fake.batches[0])
    assert fake.plan_calls[0]["start_cursor"] == 1


def test_get_or_create_makes_new_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/get-or-create",
        json={"title": "Trip Plan", "markdown": "# Trip Plan\n\nBody"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    assert res.json()["created"] is True


def test_get_or_create_returns_existing(client, active_key, fake):
    fake.existing_by_title = {"id": "doc1", "name": "Trip Plan", "url": "u", "modified_time": "t"}
    res = client.post(
        "/api/v1/tools/documents/get-or-create",
        json={"title": "Trip Plan", "markdown": "# Trip Plan\n\nBody"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] is False
    assert body["document_id"] == "doc1"
    assert fake.create_document_calls == []


def test_append_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc123/append",
        json={"markdown": "Appended text", "required_revision_id": "rev-1"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["revision_id"] == "rev-after-write"
    assert "Appended" in body["summary"]
    # appended at the position before the final paragraph's newline (75-1)
    assert fake.plan_calls[0]["start_cursor"] == 74


def test_append_revision_conflict(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc123/append",
        json={"markdown": "Appended", "required_revision_id": "stale-rev"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 409
    assert res.json()["error"] == "conflict_detected"
    assert res.json()["current_revision_id"] == "rev-1"


def test_replace_section(client, active_key, fake):
    res = client.patch(
        "/api/v1/tools/documents/doc123",
        json={
            "mode": "replace_section",
            "section_heading": "Details",
            "markdown": "New details",
            "required_revision_id": "rev-1",
        },
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert "Replaced section 'Details'" in res.json()["summary"]
    # first batch: delete the section range (heading start -> EOF)
    delete = fake.batches[0][0]["deleteContentRange"]["range"]
    assert delete["startIndex"] == 30  # Details heading start
    assert delete["endIndex"] == 75  # next heading of same level or EOF
    # plan rendered at the section start
    assert fake.plan_calls[0]["start_cursor"] == 30


def test_replace_section_mid_document_range(client, active_key, fake):
    res = client.patch(
        "/api/v1/tools/documents/doc123",
        json={
            "mode": "replace_section",
            "section_heading": "Intro",
            "markdown": "New intro",
        },
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    delete = fake.batches[0][0]["deleteContentRange"]["range"]
    assert delete["startIndex"] == 1  # Intro heading start
    assert delete["endIndex"] == 30  # next H1 (Details) start


def test_replace_section_not_found(client, active_key, fake):
    res = client.patch(
        "/api/v1/tools/documents/doc123",
        json={
            "mode": "replace_section",
            "section_heading": "Nope",
            "markdown": "x",
        },
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    assert res.json()["error"] == "invalid_request"
    assert "outline" in res.json()


def test_regenerate_requires_confirm(client, active_key, fake):
    res = client.patch(
        "/api/v1/tools/documents/doc123",
        json={"mode": "regenerate", "markdown": "# Fresh\n\nNew body"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "confirmation_required"
    assert body["preview"]["title"] == "Sample"
    assert body["preview"]["current_revision_id"] == "rev-1"
    assert body["preview"]["new_blocks"] == 2
    # nothing was written
    assert fake.batches == [] and fake.plan_calls == []


def test_regenerate_with_confirm(client, active_key, fake):
    res = client.patch(
        "/api/v1/tools/documents/doc123",
        json={
            "mode": "regenerate",
            "markdown": "# Fresh\n\nNew body",
            "confirm": True,
            "required_revision_id": "rev-1",
        },
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    delete = fake.batches[0][0]["deleteContentRange"]["range"]
    # deletes everything before the final element (keeps the trailing paragraph)
    assert delete["startIndex"] == 1
    assert delete["endIndex"] == 75
    assert fake.plan_calls[0]["start_cursor"] == 1


def test_delete_requires_confirm(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc123/delete",
        json={"confirm": False},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    assert res.json()["error"] == "confirmation_required"
    assert fake.trashed == []


def test_delete_with_confirm(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc123/delete",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.trashed == ["doc123"]
    assert "trash" in res.json()["summary"]


def test_rename_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc123/rename",
        json={"title": "New Name"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.renamed == ("doc123", "New Name")
    assert res.json()["document_id"] == "doc123"
    assert res.json()["document_url"].endswith("/doc123/edit")
    assert "New Name" in res.json()["summary"]


def test_restore_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc123/restore",
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.untrashed == ["doc123"]
    assert res.json()["document_id"] == "doc123"
    assert "restored" in res.json()["summary"]


def test_update_mode_validation(client, active_key, fake):
    res = client.patch(
        "/api/v1/tools/documents/doc123",
        json={"mode": "bogus"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    assert res.json()["error"] == "invalid_request"


def test_create_folder(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/folders",
        json={"name": "Travel/Japan 2027", "parent_id": "fldRoot"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    assert res.json()["folder_id"] == fake.folders[-1]["id"]


def test_list_folders(client, active_key, fake):
    fake.folders = [{"id": "f1", "name": "Research", "parents": ["fldRoot"]}]
    res = client.get("/api/v1/tools/folders", headers={"Authorization": f"Bearer {active_key}"})
    assert res.status_code == 200
    assert res.json()["folders"][0]["name"] == "Research"


def test_delete_folder_requires_confirm(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/folders/fld1/delete",
        json={},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422
    assert res.json()["error"] == "confirmation_required"
    assert fake.trashed == []
    res = client.post(
        "/api/v1/tools/folders/fld1/delete",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.trashed == ["fld1"]


def test_move_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc1/move",
        json={"folder_id": "fld2"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert fake.moves == [("doc1", "fld2", True)]


def test_copy_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc1/copy",
        json={"title": "Day 2 Plan"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 201
    assert res.json()["document_id"] == "copied1"


def test_export_pdf(client, active_key, fake):
    fake.export_bytes = b"%PDF-1.4 test"
    res = client.get(
        "/api/v1/tools/documents/doc1/export",
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert base64.b64decode(body["content_base64"]) == b"%PDF-1.4 test"
    assert body["mime"] == "application/pdf"


def test_export_size_cap(client, active_key, fake):
    fake.export_bytes = b"x" * (11 * 1024 * 1024)
    res = client.get(
        "/api/v1/tools/documents/doc1/export",
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422


def test_share_document(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc1/share",
        json={"email": "partner@example.com", "role": "writer"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 200
    assert res.json()["permission_id"] == "perm1"
    assert fake.shared == [("doc1", "partner@example.com", "writer", True)]


def test_share_invalid_role(client, active_key, fake):
    res = client.post(
        "/api/v1/tools/documents/doc1/share",
        json={"email": "p@example.com", "role": "owner"},
        headers={"Authorization": f"Bearer {active_key}"},
    )
    assert res.status_code == 422


def test_list_permissions(client, active_key, fake):
    fake.permissions = [{"id": "perm1", "email": "a@b.c", "role": "writer", "type": "user"}]
    res = client.get(
        "/api/v1/tools/documents/doc1/share", headers={"Authorization": f"Bearer {active_key}"}
    )
    assert res.status_code == 200
    assert res.json()["permissions"][0]["email"] == "a@b.c"


def test_key_without_google_connection_409_on_all_tools(client, user_and_headers):
    """Tool endpoints return google_not_connected without a Google connection."""
    created = client.post("/api/keys", json={"name": "grok"}, headers=user_and_headers).json()
    key = created["full_key"]
    headers = {"Authorization": f"Bearer {key}"}
    assert (
        client.post(
            "/api/v1/tools/documents/search", json={"query": "x"}, headers=headers
        ).status_code
        == 409
    )
    assert client.get("/api/v1/tools/documents/doc123", headers=headers).status_code == 409
    assert (
        client.post(
            "/api/v1/tools/documents", json={"title": "t", "markdown": "m"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.patch(
            "/api/v1/tools/documents/doc123",
            json={"mode": "regenerate", "markdown": "m"},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/tools/documents/doc123/append", json={"markdown": "m"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/tools/documents/doc123/delete", json={"confirm": True}, headers=headers
        ).status_code
        == 409
    )
