"""Named themes applied at render time. Code-defined; no DB table."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    font: str = "Arial"
    body_size: float = 10.5
    title_size: float = 26
    heading_1_size: float = 18
    heading_2_size: float = 14
    heading_3_size: float = 12
    primary_colour: str = "#7138D8"
    text_colour: str = "#252525"
    page_margin: int = 54  # points
    table_header_bg: str = "#F3F0FC"
    paragraph_spacing: int = 6  # points, space below paragraphs


THEMES: dict[str, Theme] = {
    "professional": Theme(),
}

DEFAULT_THEME = "professional"


def get_theme(name: str | None) -> Theme:
    return THEMES.get(name or DEFAULT_THEME, THEMES[DEFAULT_THEME])
