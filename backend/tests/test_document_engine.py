"""Document engine: parser AST, renderer index tracking, reader, markdown writer."""

from app.document_engine import parser
from app.document_engine.ast import Heading, ListBlock, PageBreak, Paragraph, Table
from app.document_engine.markdown_writer import ast_to_markdown
from app.document_engine.reader import document_json_to_ast
from app.document_engine.renderer import (
    BatchStep,
    TableStep,
    _table_cell_insert_index,
    render_blocks,
    render_new_document,
)
from app.document_engine.themes import get_theme


def test_parser_headings_and_inline_styles():
    doc = parser.parse(
        "# H1\n\nHello **world** and *italic* [link](https://x.example) plain\n"
    )
    assert len(doc.children) == 2
    heading = doc.children[0]
    assert isinstance(heading, Heading)
    assert heading.level == 1
    assert heading.children[0].content == "H1"

    para = doc.children[1]
    assert isinstance(para, Paragraph)
    runs = para.children
    # bold run
    bold_runs = [r for r in runs if r.bold]
    assert bold_runs and bold_runs[0].content == "world"
    italic_runs = [r for r in runs if r.italic]
    assert italic_runs and italic_runs[0].content == "italic"
    link_runs = [r for r in runs if r.href]
    assert link_runs and link_runs[0].content == "link"
    assert link_runs[0].href == "https://x.example"


def test_parser_lists_tables_pagebreak():
    md = "- a\n- b\n\n1. one\n2. two\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n---\n"
    doc = parser.parse(md)
    unordered, ordered, table, pagebreak = doc.children
    assert isinstance(unordered, ListBlock) and not unordered.ordered
    assert [i.children[0].content for i in unordered.items] == ["a", "b"]
    assert isinstance(ordered, ListBlock) and ordered.ordered
    assert isinstance(table, Table)
    assert ["".join(r.content for r in cell) for cell in table.header] == ["A", "B"]
    assert len(table.rows) == 1
    assert isinstance(pagebreak, PageBreak)


def test_parser_h4_clamped_to_h3():
    doc = parser.parse("#### deep\n")
    assert isinstance(doc.children[0], Heading)
    assert doc.children[0].level == 3


def test_table_cell_index_formula_matches_google_docs_reference():
    """Google's documented 2x2 empty table at S: cell paragraphs at
    S+3, S+5, S+8, S+10 (see the official tables how-to index table)."""
    S = 100
    assert _table_cell_insert_index(S, 0, 0, 2) == S + 3
    assert _table_cell_insert_index(S, 0, 1, 2) == S + 5
    assert _table_cell_insert_index(S, 1, 0, 2) == S + 8
    assert _table_cell_insert_index(S, 1, 1, 2) == S + 10


def test_render_blocks_paragraph_indices():
    """Sequential-state indices: text insert then styles in post-insert coords."""
    doc = parser.parse("Hello world\n")
    steps = render_blocks(doc.children, get_theme(None), start_index=1)
    assert len(steps) == 1
    assert isinstance(steps[0], BatchStep)
    requests = steps[0].requests
    assert requests[0] == {"insertText": {"location": {"index": 0}, "text": "Hello world\n"}}
    para_style = requests[1]["updateParagraphStyle"]
    assert para_style["range"] == {"startIndex": 0, "endIndex": 12}
    # run styling for "Hello world" (0..11)
    text_style = requests[2]["updateTextStyle"]
    assert text_style["range"] == {"startIndex": 0, "endIndex": 11}
    assert text_style["textStyle"]["fontSize"]["magnitude"] == 10.5
    assert "fontFamily" in text_style["fields"]
    # cursor advanced by 12 (absolute virtual position: 1 + 12)
    assert steps[0].cursor == 13


def test_render_blocks_heading_named_style():
    doc = parser.parse("## Section\n")
    steps = render_blocks(doc.children, get_theme(None), start_index=1)
    requests = steps[0].requests
    assert requests[0]["insertText"]["text"] == "Section\n"
    assert requests[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_2"
    assert requests[2]["updateTextStyle"]["textStyle"]["fontSize"]["magnitude"] == 14


def test_render_list_emits_bullets_after_all_items():
    doc = parser.parse("- a\n- b\n")
    steps = render_blocks(doc.children, get_theme(None), start_index=1)
    requests = steps[0].requests
    assert [r["insertText"]["text"] for r in requests if "insertText" in r] == ["a\n", "b\n"]
    bullets = [r["createParagraphBullets"] for r in requests if "createParagraphBullets" in r]
    assert len(bullets) == 1
    assert bullets[0]["range"] == {"startIndex": 0, "endIndex": 4}
    assert bullets[0]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"


def test_render_table_emits_table_step_and_resumes_cursor():
    doc = parser.parse("| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter\n")
    steps = render_blocks(doc.children, get_theme(None), start_index=1)
    assert isinstance(steps[0], TableStep)
    assert steps[0].rows == 2 and steps[0].cols == 2
    # predicted table size for 2x2: 2*(2*2+1) + 2 = 12
    assert steps[0].cursor == steps[0].start_cursor + 12
    # content after the table resumes at the predicted cursor
    assert isinstance(steps[1], BatchStep)
    assert steps[1].start_cursor == steps[0].cursor
    assert steps[1].requests[0]["insertText"]["text"] == "After\n"


def test_render_new_document_inserts_title_once():
    from app.document_engine.ast import Document

    ast = parser.parse("# Report\n\nBody\n")
    steps, doc_style = render_new_document(
        Document(title="Report", children=ast.children), get_theme(None)
    )
    # markdown already opens with the title -> no duplicate title paragraph
    assert len(steps) == 1
    texts = [r["insertText"]["text"] for r in steps[0].requests if "insertText" in r]
    assert texts.count("Report\n") == 1  # the markdown's own H1, no added title
    assert "Body\n" in texts
    assert doc_style  # document style (margins) requests present

    ast2 = parser.parse("Body only\n")
    steps2, _ = render_new_document(
        Document(title="Report", children=ast2.children), get_theme(None)
    )
    texts2 = [r["insertText"]["text"] for r in steps2[0].requests if "insertText" in r]
    assert texts2[0] == "Report\n"


def test_reader_converts_google_json_to_ast():
    doc_json = {
        "title": "Sample",
        "revisionId": "rev-1",
        "body": {
            "content": [
                {"sectionBreak": {}},
                {
                    "startIndex": 1,
                    "endIndex": 10,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": "TITLE"},
                        "elements": [
                            {"textRun": {"content": "Sample\n", "textStyle": {}}}
                        ],
                    },
                },
                {
                    "startIndex": 10,
                    "endIndex": 30,
                    "paragraph": {
                        "elements": [
                            {
                                "textRun": {
                                    "content": "Bold part\n",
                                    "textStyle": {"bold": True},
                                }
                            }
                        ]
                    },
                },
                {
                    "startIndex": 30,
                    "endIndex": 40,
                    "paragraph": {
                        "elements": [
                            {"pageBreak": {}},
                            {"textRun": {"content": "\n", "textStyle": {}}},
                        ]
                    },
                },
            ]
        },
    }
    ast, outline = document_json_to_ast(doc_json)
    assert ast.title == "Sample"
    assert isinstance(ast.children[0], Heading)
    assert ast.children[0].level == 1
    assert ast.children[0].children[0].content == "Sample"
    assert isinstance(ast.children[1], Paragraph)
    assert ast.children[1].children[0].bold is True
    assert ast.children[1].children[0].content == "Bold part"
    assert isinstance(ast.children[2], PageBreak)
    assert len(outline) == 1
    assert outline[0].heading == "Sample"
    assert outline[0].start_index == 1


def _cell(text: str, bold: bool = False) -> dict:
    return {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": text + "\n", "textStyle": {"bold": bold}}}
                    ]
                }
            }
        ]
    }


def test_reader_tables_and_lists():
    doc_json = {
        "title": "",
        "body": {
            "content": [
                {
                    "startIndex": 1,
                    "endIndex": 50,
                    "paragraph": {
                        "elements": [{"textRun": {"content": "Item\n", "textStyle": {}}}],
                        "bullet": {"listId": "L1"},
                    },
                },
                {
                    "startIndex": 50,
                    "endIndex": 90,
                    "table": {
                        "tableRows": [
                            {"tableCells": [_cell("A", bold=True), _cell("B", bold=True)]},
                            {"tableCells": [_cell("1"), _cell("2")]},
                        ]
                    },
                },
            ]
        },
        "lists": {"L1": {"listProperties": {"nestingLevels": [{"glyphType": "GLYPH_DECIMAL"}]}}},
    }
    ast, _outline = document_json_to_ast(doc_json)
    list_block = ast.children[0]
    assert isinstance(list_block, ListBlock) and list_block.ordered
    assert list_block.items[0].children[0].content == "Item"
    table = ast.children[1]
    assert isinstance(table, Table)
    assert len(table.header) == 2  # first row is all-bold -> header
    assert len(table.rows) == 1


def test_markdown_writer_round_trip():
    md = (
        "# Title\n\nHello **world** and [link](https://x.example)\n\n"
        "- a\n- b\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    )
    doc = parser.parse(md)
    out = ast_to_markdown(doc)
    reparsed = parser.parse(out)
    assert isinstance(reparsed.children[0], Heading)
    assert reparsed.children[0].level == 1
    assert isinstance(reparsed.children[1], Paragraph)
    bold = [r for r in reparsed.children[1].children if r.bold]
    assert bold[0].content == "world"
    assert isinstance(reparsed.children[2], ListBlock)
    assert isinstance(reparsed.children[3], Table)
