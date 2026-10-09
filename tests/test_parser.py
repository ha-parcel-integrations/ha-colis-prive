"""Tests for the strict result-panel parser."""
import pytest

from custom_components.colis_prive.parser import (
    ColisPrivParseError,
    clean_text,
    parse_result,
)

from .payloads import (
    FAKE_ADDRESS,
    FAKE_NAME_FRAGMENT,
    SENTENCES,
    result_html,
    rows_newest_first,
)


def test_parses_sender_status_history_and_npai():
    result = parse_result(result_html())

    assert result["sender"] == "EXAMPLE SHOP"
    assert result["statusText"] == SENTENCES["en"]["out_for_delivery"]
    assert result["npai"] == "0"
    assert result["history"] == rows_newest_first("en")


def test_history_keeps_page_order_newest_first():
    result = parse_result(result_html())
    assert [row["date"] for row in result["history"]] == [
        "04/03/2026",
        "03/03/2026",
        "03/03/2026",
        "02/03/2026",
    ]


def test_leading_spacer_row_is_ignored():
    with_spacer = parse_result(result_html(spacer=True))
    without = parse_result(result_html(spacer=False))
    assert with_spacer["history"] == without["history"]


def test_whitespace_br_and_nbsp_are_normalised():
    rows = [{"date": "04/03/2026", "text": "Line one<br>line\n   two&nbsp;end"}]
    result = parse_result(result_html(rows=rows))
    assert result["history"][0]["text"] == "Line one line two end"
    assert result["statusText"] == "Line one line two end"


def test_curly_apostrophes_survive_parsing_verbatim():
    rows = [{"date": "04/03/2026", "text": "préparation par l’expéditeur"}]
    assert parse_result(result_html(rows=rows))["statusText"] == "préparation par l’expéditeur"


def test_missing_npai_input_is_none():
    assert parse_result(result_html(npai=None))["npai"] is None


def test_missing_sender_is_none():
    html = result_html().replace("<span>EXAMPLE SHOP</span>", "")
    assert parse_result(html)["sender"] is None


def test_recipient_block_is_never_captured():
    html = result_html()
    assert FAKE_ADDRESS in html
    result = parse_result(html)
    assert FAKE_NAME_FRAGMENT not in repr(result)
    assert "Imaginaire" not in repr(result)
    assert "second line" not in repr(result)


def test_recipient_block_with_nested_and_unclosed_markup_stays_skipped():
    html = result_html(address="<b>Fiktiva</b> <p>Rue <i>Imaginaire</i> 99")
    assert "Fiktiva" not in repr(parse_result(html))


def test_recipient_status_lookalike_does_not_become_the_status():
    html = result_html().replace(
        '<td class="tdTitre">To</td>\n      <td class="tdText">',
        '<td class="tdTitre">To</td>\n      <td class="tdText divStatut">',
    )
    assert parse_result(html)["statusText"] == SENTENCES["en"]["out_for_delivery"]


def test_missing_panel_fails_closed():
    with pytest.raises(ColisPrivParseError, match="panel"):
        parse_result(result_html(panel=False))


def test_missing_status_cell_fails_closed():
    html = result_html().replace("divStatut", "divOther")
    with pytest.raises(ColisPrivParseError, match="status"):
        parse_result(html)


@pytest.mark.parametrize("missing", ["th-date", "th-statut"])
def test_missing_header_id_fails_closed(missing):
    html = result_html().replace(f'id="{missing}"', 'id="th-other"')
    with pytest.raises(ColisPrivParseError, match="header"):
        parse_result(html)


def test_missing_headers_entirely_fail_closed():
    with pytest.raises(ColisPrivParseError, match="header"):
        parse_result(result_html(header=False))


@pytest.mark.parametrize(
    "row",
    [
        {"date": "2026-03-04", "text": "x"},
        {"date": "4/3/2026", "text": "x"},
        {"date": "04/03/2026", "text": ""},
    ],
)
def test_malformed_history_row_fails_closed(row):
    with pytest.raises(ColisPrivParseError, match="row"):
        parse_result(result_html(status="status", rows=[row]))


def test_row_without_its_cells_fails_closed():
    html = result_html().replace('headers="th-statut"', 'headers="x"')
    with pytest.raises(ColisPrivParseError, match="row"):
        parse_result(html)


def test_unrelated_stray_end_tag_is_tolerated():
    assert parse_result(result_html() + "</span></td>")["sender"] == "EXAMPLE SHOP"


def test_self_closing_non_void_tags_do_not_leak_stack_state():
    html = result_html().replace("<h1>", "<div/><h1>")
    assert parse_result(html)["sender"] == "EXAMPLE SHOP"


def test_void_tags_never_open_scope():
    html = result_html().replace("<h1>", "<br><img src='x'><hr><h1>")
    assert parse_result(html)["sender"] == "EXAMPLE SHOP"


def test_parser_crash_becomes_parse_error(monkeypatch):
    from custom_components.colis_prive import parser

    def _boom(self, data):
        raise AssertionError("boom")

    monkeypatch.setattr(parser._PanelParser, "feed", _boom)
    with pytest.raises(ColisPrivParseError, match="unparseable"):
        parse_result("<html></html>")


def test_clean_text_collapses_whitespace():
    assert clean_text(" a \xa0\n b ") == "a b"


def test_stray_void_end_tag_is_ignored():
    assert parse_result(result_html() + "</br>")["sender"] == "EXAMPLE SHOP"
