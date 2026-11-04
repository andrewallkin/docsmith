"""Typed document AST produced by parser.py and consumed by renderer.py."""

from dataclasses import dataclass, field


@dataclass
class Text:
    content: str
    bold: bool = False
    italic: bool = False
    href: str | None = None


@dataclass
class Paragraph:
    children: list[Text] = field(default_factory=list)


@dataclass
class Heading:
    level: int  # 1..3 (clamped by the parser)
    children: list[Text] = field(default_factory=list)


@dataclass
class ListItem:
    children: list[Text] = field(default_factory=list)


@dataclass
class ListBlock:
    ordered: bool
    items: list[ListItem] = field(default_factory=list)


@dataclass
class Table:
    # header cells: each cell is a list of Text runs
    header: list[list[Text]] = field(default_factory=list)
    rows: list[list[list[Text]]] = field(default_factory=list)


@dataclass
class PageBreak:
    pass


Block = Paragraph | Heading | ListBlock | Table | PageBreak


@dataclass
class Document:
    title: str | None = None
    children: list[Block] = field(default_factory=list)
