# Google Sheets

Docsmith's tool API can create, read, and manage Google Sheets alongside Google
Docs. Same auth as everywhere else: `Authorization: Bearer ds_live_...`, all
paths relative to `/api/v1`.

Full endpoint reference: `GET /api/v1/tools/docs` (markdown, no auth).

## Setup

The Google Cloud project (see `.env.example`, Google OAuth section) must have
the **Google Sheets API**, **Google Docs API**, and **Google Drive API**
enabled.

## What the agent can do

- **Spreadsheet CRUD** — create (optional tab list and `folder_id`), search,
  get metadata (title, url, tabs with row/column counts), delete/restore,
  rename, move, copy, share.
- **Values** — read (`values`, `formulas`, or `both`; output as `json`, `csv`,
  or `markdown`), write, append, clear.
- **Find and replace** — across one tab, or every tab when `sheet` is omitted.
- **Tabs** — add, rename, hide/unhide, delete (deleting the only tab is
  refused).
- **Export** — `csv`, `xlsx`, or `pdf` (base64).
- **Folders** — the `/tools/folders` endpoints work for spreadsheets too, and
  `POST /tools/spreadsheets` accepts `folder_id` to file it immediately.

## Example

```bash
# Create a spreadsheet with two tabs
curl -X POST https://docsmith.example.com/api/v1/tools/spreadsheets \
  -H "Authorization: Bearer ds_live_..." -H "Content-Type: application/json" \
  -d '{"title": "Q4 report", "sheets": ["Summary", "Data"]}'

# Write a formula
curl -X POST .../tools/spreadsheets/{id}/values \
  -H "Authorization: Bearer ds_live_..." -H "Content-Type: application/json" \
  -d '{"sheet": "Summary", "range": "F2", "rows": [["=SUM(B2:B10)"]]}'

# Read a range as markdown
curl ".../tools/spreadsheets/{id}/values?sheet=Data&range=A1:D10&format=markdown"
```

## Ranges, reads, writes, and formulas

- **A1 notation**: `Sheet1!A1:D10` is a rectangle, `A:D` is a whole column,
  and omitting `range` reads a whole tab. Writes require an explicit bounded
  range like `A1:D10` or a single cell.
- **Reads** are capped at 200,000 cells per request — page large tabs with
  the `range` parameter.
- **Writes auto-grow** the grid when rows extend past it; reads beyond the
  grid fail.
- **Formulas**: send any string starting with `=` (e.g. `=SUM(B2:B10)`) and
  Google recalculates it on write — the default write `mode` is
  `user_entered` (`raw` writes the string verbatim).

## Destructive operations need `confirm: true`

Four spreadsheet operations are confirm-gated. Without `confirm: true` they
return **422 with a preview** of what would change and touch nothing:

- clear a range (`POST .../values/clear`)
- find and replace (`POST .../find-replace`)
- delete a tab (`DELETE .../sheets/{name}`)
- delete a spreadsheet (`POST .../delete`)
