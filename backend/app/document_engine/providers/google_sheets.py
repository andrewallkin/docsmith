"""Google Sheets v4 operations backed by the shared Drive provider."""

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.document_engine.providers.google_drive_base import (
    MAX_ATTEMPTS,
    RETRYABLE_STATUS,
    DriveFileOps,
    _with_retries,
)


class SpreadsheetNotFound(Exception):
    """Spreadsheet missing or not accessible to the connected account."""


class SheetApiError(Exception):
    """A Sheets API request failed or could not be constructed."""


def _sheets_request(call, *, spreadsheet_id: str | None = None, read: bool = False):
    try:
        return _with_retries(call)
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else None
        if status == 404 and read:
            raise SpreadsheetNotFound(spreadsheet_id) from exc
        if status == 403:
            raise SheetApiError(
                "Google Sheets API 403: the file isn't accessible to the connected account."
            ) from exc
        raise SheetApiError(f"Google Sheets API {status}: {exc.reason}") from exc


class GoogleSheetsProvider(DriveFileOps):
    RETRYABLE_STATUS = RETRYABLE_STATUS
    MAX_ATTEMPTS = MAX_ATTEMPTS

    @property
    def sheets(self):
        if not hasattr(self, "_sheets") or self._sheets is None:
            self._sheets = build(
                "sheets", "v4", credentials=self._credentials, cache_discovery=False
            )
        return self._sheets

    def get_spreadsheet(self, spreadsheet_id: str) -> dict:
        return _sheets_request(
            lambda: self.sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute(),
            spreadsheet_id=spreadsheet_id,
            read=True,
        )

    def create_spreadsheet(self, title: str, sheet_titles: list[str]) -> dict:
        body: dict = {"properties": {"title": title}}
        if sheet_titles:
            body["sheets"] = [{"properties": {"title": name}} for name in sheet_titles]
        return _sheets_request(lambda: self.sheets.spreadsheets().create(body=body).execute())

    def get_values(self, spreadsheet_id: str, range_a1: str) -> list[list]:
        result = _sheets_request(
            lambda: (
                self.sheets.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_a1)
                .execute()
            ),
            spreadsheet_id=spreadsheet_id,
            read=True,
        )
        return result.get("values", [])

    def get_values_with_formulas(self, spreadsheet_id: str, range_a1: str) -> dict:
        results = []
        for option in ("UNFORMATTED_VALUE", "FORMULA"):
            result = _sheets_request(
                lambda option=option: (
                    self.sheets.spreadsheets()
                    .values()
                    .get(spreadsheetId=spreadsheet_id, range=range_a1, valueRenderOption=option)
                    .execute()
                ),
                spreadsheet_id=spreadsheet_id,
                read=True,
            )
            results.append(result.get("values", []))
        return {"values": results[0], "formulas": results[1]}

    def update_values(self, spreadsheet_id: str, range_a1: str, rows: list[list], mode: str) -> int:
        options = {"user_entered": "USER_ENTERED", "raw": "RAW"}
        if mode not in options:
            raise SheetApiError(f"Invalid value input mode: {mode}")
        result = _sheets_request(
            lambda: (
                self.sheets.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_a1,
                    valueInputOption=options[mode],
                    body={"values": rows},
                )
                .execute()
            )
        )
        return result.get("updatedCells", 0)

    def append_values(self, spreadsheet_id: str, sheet: str, rows: list[list]) -> str:
        result = _sheets_request(
            lambda: (
                self.sheets.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet}!A1",
                    insertDataOption="INSERT_ROWS",
                    valueInputOption="USER_ENTERED",
                    body={"values": rows},
                )
                .execute()
            )
        )
        return result["updates"]["updatedRange"]

    def clear_values(self, spreadsheet_id: str, range_a1: str) -> None:
        _sheets_request(
            lambda: (
                self.sheets.spreadsheets()
                .values()
                .clear(spreadsheetId=spreadsheet_id, range=range_a1, body={})
                .execute()
            )
        )

    def batch_update(self, spreadsheet_id: str, requests: list[dict]) -> dict:
        return _sheets_request(
            lambda: (
                self.sheets.spreadsheets()
                .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
                .execute()
            )
        )

    def ensure_grid(self, spreadsheet_id: str, sheet_id: int, rows: int, cols: int) -> None:
        self.batch_update(
            spreadsheet_id,
            [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"rowCount": rows, "columnCount": cols},
                        },
                        "fields": "gridProperties.rowCount,gridProperties.columnCount",
                    }
                }
            ],
        )

    def find_replace(
        self,
        spreadsheet_id: str,
        sheet_name: str | None,
        find: str,
        replace: str,
        match_case: bool,
        whole_cell: bool,
    ) -> int:
        request = {
            "find": find,
            "replacement": replace,
            "matchCase": match_case,
            "matchEntireCell": whole_cell,
        }
        if sheet_name is None:
            request["allSheets"] = True
        else:
            metadata = self.get_spreadsheet(spreadsheet_id)
            for sheet in metadata.get("sheets", []):
                properties = sheet.get("properties", {})
                if properties.get("title") == sheet_name:
                    request["sheetId"] = properties["sheetId"]
                    break
            else:
                raise SheetApiError(f"Sheet not found: {sheet_name}")
        result = self.batch_update(spreadsheet_id, [{"findReplace": request}])
        replies = result.get("replies", [])
        return replies[0].get("findReplace", {}).get("occurrencesChanged", 0) if replies else 0
