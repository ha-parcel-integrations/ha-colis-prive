"""Strict parser for the Colis Privé result panel.

There is no structured alternative to the tracking page, so this parser fails
closed: anything it cannot positively identify raises
:class:`ColisPrivParseError` instead of yielding a partial parcel. The block
holding the recipient's name and address is skipped while parsing, so that text
never reaches a variable, a log line or the stored payload.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

PANEL_ID = "ctl00_CDC_pnResultatColis"
_NPAI_ID_SUFFIX = "hfNPAI"
_RECIPIENT_CLASS = "divDesti"
_VOID_TAGS = frozenset(
    {"br", "hr", "img", "input", "link", "meta", "area", "base", "col", "wbr"}
)
_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")


class ColisPrivParseError(Exception):
    """Raised when the page does not have the expected result-panel shape."""


def clean_text(value: str) -> str:
    """Collapse whitespace (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


class _Node:
    """One open element on the parser stack."""

    __slots__ = ("tag", "classes", "ident", "role", "skip", "parts")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.classes = frozenset(attrs.get("class", "").split())
        self.ident = attrs.get("id", "")
        self.role: str | None = None
        self.skip = _RECIPIENT_CLASS in self.classes
        self.parts: list[str] = []


class _PanelParser(HTMLParser):
    """Collect sender, status sentence, history rows and the NPAI flag."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_Node] = []
        self.panel_seen = False
        self.header_ids: set[str] = set()
        self.sender: str | None = None
        self.status: str | None = None
        self.npai: str | None = None
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None

    def _skipping(self) -> bool:
        return any(node.skip for node in self._stack)

    def _in_panel(self) -> bool:
        return any(node.ident == PANEL_ID for node in self._stack)

    def _ancestor_with_class(self, name: str) -> bool:
        return any(name in node.classes for node in self._stack)

    def handle_starttag(self, tag, attrs):
        attr_map = {key: value or "" for key, value in attrs}
        node = _Node(tag, attr_map)
        if self._skipping():
            if tag not in _VOID_TAGS:
                self._stack.append(node)
            return

        if node.ident == PANEL_ID:
            self.panel_seen = True
        if tag == "input" and attr_map.get("id", "").endswith(_NPAI_ID_SUFFIX):
            self.npai = attr_map.get("value", "")
        if node.ident in ("th-date", "th-statut"):
            self.header_ids.add(node.ident)

        if tag not in _VOID_TAGS:
            self._assign_role(node, attr_map)
            self._stack.append(node)
        elif tag == "br":
            self._append(" ")

    def _assign_role(self, node: _Node, attrs: dict[str, str]) -> None:
        if not self._in_panel():
            return
        parent = self._stack[-1] if self._stack else None
        if node.tag == "span" and parent is not None and parent.tag == "h1":
            if self.sender is None:
                node.role = "sender"
        elif "tdText" in node.classes and self._ancestor_with_class("divStatut"):
            if self.status is None:
                node.role = "status"
        elif node.tag == "tr" and "bandeauText" in node.classes:
            node.role = "row"
            self._row = {}
        elif node.tag == "td" and self._row is not None:
            headers = attrs.get("headers", "")
            if headers == "th-date":
                node.role = "row_date"
            elif headers == "th-statut":
                node.role = "row_text"

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID_TAGS:
            self.handle_starttag(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def _append(self, text: str) -> None:
        for node in reversed(self._stack):
            if node.role in ("sender", "status", "row_date", "row_text"):
                node.parts.append(text)
                return

    def handle_data(self, data):
        if self._skipping():
            return
        self._append(data)

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        index = next(
            (i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i].tag == tag),
            None,
        )
        if index is None:
            return
        closed = self._stack[index:]
        del self._stack[index:]
        for node in reversed(closed):
            self._finish(node)

    def _finish(self, node: _Node) -> None:
        if node.skip or node.role is None:
            return
        text = clean_text("".join(node.parts))
        if node.role == "sender":
            self.sender = text or None
        elif node.role == "status":
            self.status = text or None
        elif node.role == "row_date" and self._row is not None:
            self._row["date"] = text
        elif node.role == "row_text" and self._row is not None:
            self._row["text"] = text
        elif node.role == "row" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def parse_result(html: str) -> dict:
    """Parse a ``detailColis.aspx`` page into sender, status, history and NPAI.

    ``history`` keeps the table's own order (newest first) and each entry is
    ``{"date": "DD/MM/YYYY", "text": ...}``. Raises :class:`ColisPrivParseError`
    when the panel, the status cell, the history header ids or any history cell
    is missing or malformed.
    """
    parser = _PanelParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as err:  # noqa: BLE001 - html.parser can raise assorted errors
        raise ColisPrivParseError("unparseable markup") from err

    if not parser.panel_seen:
        raise ColisPrivParseError("result panel not found")
    if not parser.status:
        raise ColisPrivParseError("status cell not found")
    if parser.header_ids != {"th-date", "th-statut"}:
        raise ColisPrivParseError("history header ids not found")
    for row in parser.rows:
        if not _DATE_RE.fullmatch(row.get("date", "")) or not row.get("text"):
            raise ColisPrivParseError("malformed history row")

    return {
        "sender": parser.sender,
        "statusText": parser.status,
        "history": parser.rows,
        "npai": parser.npai,
    }
