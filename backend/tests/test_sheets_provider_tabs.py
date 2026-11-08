"""Sheets provider Drive-level contracts (search / copy / metadata), stub client."""

import pytest
from test_sheets_provider import StubSheets, http_error

from app.document_engine.providers.google_drive_base import DocNotFound
from app.document_engine.providers.google_sheets import (
    SPREADSHEET_URL,
    GoogleSheetsProvider,
)


@pytest.fixture
def provider():
    # One stub client stands in for both the Sheets v4 and Drive v3 resources
    # (injected like test_document_engine.py injects fake drive clients).
    client = StubSheets()
    sheets = GoogleSheetsProvider(object())
    object.__setattr__(sheets, "_sheets", client)
    object.__setattr__(sheets, "_drive", client)
    return sheets, client


def test_search_spreadsheets_uses_spreadsheet_mime_and_url(provider):
    sheets, client = provider
    client.responses = [{"files": [{"id": "id1", "name": "Budget", "modifiedTime": "t1"}]}]
    result = sheets.search_spreadsheets("Budget", 5, folder_id="folder1")
    assert result == [
        {
            "id": "id1",
            "name": "Budget",
            "url": SPREADSHEET_URL.format("id1"),
            "modified_time": "t1",
        }
    ]
    assert client.calls == [
        (
            "list",
            {
                "q": (
                    "name contains 'Budget' and 'folder1' in parents and "
                    "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
                ),
                "pageSize": 5,
                "fields": "files(id,name,modifiedTime,mimeType)",
            },
        )
    ]


def test_search_spreadsheets_without_query_or_folder(provider):
    sheets, client = provider
    client.responses = [{"files": []}]
    assert sheets.search_spreadsheets("", 10, folder_id=None) == []
    assert client.calls == [
        (
            "list",
            {
                "q": "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false",
                "pageSize": 10,
                "fields": "files(id,name,modifiedTime,mimeType)",
            },
        )
    ]


def test_copy_spreadsheet_returns_spreadsheet_url(provider):
    sheets, client = provider
    client.responses = [{"id": "copy1", "name": "Budget copy"}]
    result = sheets.copy_spreadsheet("id1", "Budget copy")
    assert result == {
        "id": "copy1",
        "name": "Budget copy",
        "url": SPREADSHEET_URL.format("copy1"),
    }
    assert client.calls == [("copy", {"fileId": "id1", "body": {"name": "Budget copy"}})]


def test_get_file_metadata_returns_name_and_modified_time(provider):
    sheets, client = provider
    client.responses = [{"id": "id1", "name": "Budget", "modifiedTime": "2026-11-01T00:00:00Z"}]
    result = sheets.get_file_metadata("id1")
    assert result == {
        "id": "id1",
        "name": "Budget",
        "modified_time": "2026-11-01T00:00:00Z",
    }
    assert client.calls == [("get", {"fileId": "id1", "fields": "id,name,modifiedTime"})]


def test_get_file_metadata_404_raises_doc_not_found(provider):
    sheets, client = provider
    client.responses = [http_error(404, "Not Found")]
    with pytest.raises(DocNotFound):
        sheets.get_file_metadata("id1")
