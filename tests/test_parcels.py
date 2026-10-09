"""Tests for the pure parcel-mapping helpers."""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.colis_prive import parcels
from custom_components.colis_prive.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.colis_prive.parcels import (
    _KEYWORDS,
    _fold,
    apply_delivered_filter,
    build_history,
    format_dimensions,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    resolve_lang,
    row_timestamp,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
)

from .payloads import (
    DELIVERED_TEXT,
    EXPECTED,
    POSTCODE,
    SENTENCES,
    STAGES_OLDEST_FIRST,
    active_sample,
    delivered_sample,
    in_transit_sample,
    parsed,
    rows_newest_first,
)

# ---------------------------------------------------------------------------
# status mapping: three tables, never mixed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["fr", "nl", "en"])
@pytest.mark.parametrize("stage", STAGES_OLDEST_FIRST)
def test_every_observed_sentence_maps_in_its_own_language(lang, stage):
    assert map_parcel_status(SENTENCES[lang][stage], lang) == ParcelStatus(EXPECTED[stage])


@pytest.mark.parametrize("lang", ["fr", "nl", "en"])
def test_each_sentence_matches_exactly_one_keyword(lang):
    """The overlapping sentences are why the keywords are as long as they are."""
    table = parcels._TABLES[lang]
    for stage in STAGES_OLDEST_FIRST:
        folded = _fold(SENTENCES[lang][stage])
        assert sum(keyword in folded for keyword, _ in table) == 1, stage


def test_overlapping_sentences_keep_their_own_stage():
    fr = SENTENCES["fr"]
    assert map_parcel_status(fr["taken_over"], "fr") == ParcelStatus.IN_TRANSIT
    assert map_parcel_status(fr["arrived"], "fr") == ParcelStatus.IN_TRANSIT
    assert "agence régionale" in _fold(fr["taken_over"])
    nl = SENTENCES["nl"]
    assert "regionale bezorgpartner" in _fold(nl["taken_over"])
    assert map_parcel_status(nl["taken_over"], "nl") == ParcelStatus.IN_TRANSIT


def test_tables_are_not_translated_or_borrowed():
    """A sentence in another language's table is unknown, not translated."""
    assert map_parcel_status(SENTENCES["fr"]["out_for_delivery"], "en") == ParcelStatus.UNKNOWN
    assert map_parcel_status(SENTENCES["en"]["registered"], "nl") == ParcelStatus.UNKNOWN
    assert map_parcel_status(SENTENCES["nl"]["arrived"], "fr") == ParcelStatus.UNKNOWN


def test_unknown_language_has_no_table():
    assert map_parcel_status(SENTENCES["en"]["registered"], "de") == ParcelStatus.UNKNOWN


def test_keyword_tables_match_the_observed_keywords():
    assert {lang: [k for k, _ in table] for lang, table in _KEYWORDS.items()} == {
        "fr": [
            "livré en boîte aux lettres",
            "en cours de distribution",
            "arrivé sur notre agence régionale",
            "pris en charge par Colis Privé",
            "préparation par l'expéditeur",
        ],
        "nl": [
            "in de brievenbus bezorgd",
            "onderweg naar jou",
            "toegekomen bij onze regionale bezorgpartner",
            "nationaal platform",
            "klaargemaakt door de webshop",
        ],
        "en": [
            "delivered in letterbox",
            "being delivered by the driver",
            "arrived at our regional distribution office",
            "well received by a Colis Privé branch",
            "prepared by your webmerchant",
        ],
    }


@pytest.mark.parametrize("apostrophe", ["'", "\u2019", "\u2018", "\u02bc"])
def test_apostrophe_variants_and_spacing_match(apostrophe):
    text = f"Votre  colis est en cours de   préparation par l{apostrophe}expéditeur"
    assert map_parcel_status(text, "fr") == ParcelStatus.REGISTERED


def test_matching_ignores_case_and_nbsp():
    assert map_parcel_status("YOUR PARCEL IS BEING DELIVERED\xa0BY THE DRIVER", "en") == (
        ParcelStatus.OUT_FOR_DELIVERY
    )


@pytest.mark.parametrize(
    ("language", "lang"),
    [
        ("fr", "fr"),
        ("fr-CA", "fr"),
        ("fr_BE", "fr"),
        ("nl", "nl"),
        ("nl-BE", "nl"),
        ("de", "en"),
        ("en-GB", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_resolve_lang(language, lang):
    assert resolve_lang(language) == lang


def test_missing_status_is_unknown_silently(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status(None, "en") == ParcelStatus.UNKNOWN
        assert map_parcel_status("", "en") == ParcelStatus.UNKNOWN
    assert caplog.records == []


def test_unmapped_status_is_unknown_with_one_warning_naming_the_language(caplog):
    with caplog.at_level(logging.WARNING):
        assert map_parcel_status("Teleported to Mars", "nl") == ParcelStatus.UNKNOWN
        assert map_parcel_status("Teleported  to Mars", "nl") == ParcelStatus.UNKNOWN
    warnings = [r for r in caplog.records if "Unrecognised" in r.getMessage()]
    assert len(warnings) == 1
    assert "lang=nl" in warnings[0].getMessage()
    assert "Teleported to Mars" in warnings[0].getMessage()
    assert "unrecognised_status.yml" in warnings[0].getMessage()


def test_same_sentence_in_another_language_warns_again(caplog):
    with caplog.at_level(logging.WARNING):
        map_parcel_status("Teleported to Mars", "nl")
        map_parcel_status("Teleported to Mars", "en")
    assert len([r for r in caplog.records if "Unrecognised" in r.getMessage()]) == 2


def test_unmapped_current_sentence_falls_back_to_newest_mappable_history(caplog):
    history = ["Teleported to Mars", SENTENCES["en"]["arrived"], SENTENCES["en"]["registered"]]
    with caplog.at_level(logging.WARNING):
        status = map_parcel_status("Teleported to Mars", "en", history)
    assert status == ParcelStatus.IN_TRANSIT
    assert any("reported as 'in_transit'" in r.getMessage() for r in caplog.records)


def test_unmapped_everywhere_is_unknown(caplog):
    with caplog.at_level(logging.WARNING):
        status = map_parcel_status("Mystery", "en", ["Another mystery"])
    assert status == ParcelStatus.UNKNOWN
    assert any("reported as 'unknown'" in r.getMessage() for r in caplog.records)


def test_map_event_status_missing_and_unmapped_are_none(caplog):
    assert map_event_status(None, "en") is None
    assert map_event_status("", "en") is None
    with caplog.at_level(logging.WARNING):
        assert map_event_status("Mystery", "en") is None
    assert any("history entry left unmapped" in r.getMessage() for r in caplog.records)
    assert map_event_status(SENTENCES["fr"]["registered"], "fr") == ParcelStatus.REGISTERED



# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_row_timestamp_is_local_midnight_per_country():
    assert row_timestamp("04/03/2026", "FR") == "2026-03-04T00:00:00+01:00"
    assert row_timestamp("04/03/2026", "BE") == "2026-03-04T00:00:00+01:00"
    assert row_timestamp("04/03/2026", "LU") == "2026-03-04T00:00:00+01:00"
    # Summer time differs by offset, not by calendar day.
    assert row_timestamp("04/07/2026", "BE") == "2026-07-04T00:00:00+02:00"
    assert row_timestamp("04/07/2026", "XX") == "2026-07-04T00:00:00+02:00"


@pytest.mark.parametrize("bad", ["", "31/02/2026", "x/y/z", "04-03-2026", None])
def test_row_timestamp_rejects_garbage(bad):
    assert row_timestamp(bad, "FR") is None


def test_build_history_orders_same_day_rows_by_lifecycle_not_page_order():
    # As seen on a real parcel: after delivery the page listed "taken over"
    # above "arrived" although it happened first, all on the same date.
    texts = SENTENCES["fr"]
    rows = [
        {"date": "03/03/2026", "text": texts[stage]}
        for stage in ("delivered", "out_for_delivery", "taken_over", "arrived", "registered")
    ]
    events = build_history(rows, "fr", "BE")
    assert [e["raw_status"] for e in events] == [
        texts[stage]
        for stage in ("registered", "taken_over", "arrived", "out_for_delivery", "delivered")
    ]


def test_build_history_keeps_an_unmapped_row_next_to_its_neighbour():
    texts = SENTENCES["en"]
    rows = [
        {"date": "03/03/2026", "text": texts["out_for_delivery"]},
        {"date": "03/03/2026", "text": "A sentence nobody has seen"},
        {"date": "03/03/2026", "text": texts["taken_over"]},
        {"date": "02/03/2026", "text": texts["registered"]},
    ]
    events = build_history(rows, "en", "BE")
    assert [e["raw_status"] for e in events] == [
        texts["registered"],
        texts["taken_over"],
        "A sentence nobody has seen",
        texts["out_for_delivery"],
    ]


def test_build_history_reverses_to_oldest_first_and_keeps_same_day_order():
    events = build_history(rows_newest_first("en"), "en", "BE")

    assert [e["raw_status"] for e in events] == [
        SENTENCES["en"][stage] for stage in STAGES_OLDEST_FIRST
    ]
    # The two events of 03/03 keep their table order; a timestamp sort would
    # have swapped or lost it.
    assert events[1]["timestamp"] == events[2]["timestamp"]
    assert [e["status"] for e in events] == [
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
    ]


def test_build_history_caps_to_the_most_recent_events():
    rows = [{"date": "01/03/2026", "text": f"event {n}"} for n in range(1, 26)]
    events = build_history(rows, "en", "FR")
    assert len(events) == 20
    assert events[-1]["raw_status"] == "event 1"
    assert events[0]["raw_status"] == "event 20"


def test_build_history_skips_malformed_rows_and_handles_none():
    rows = ["junk", {"date": "bad", "text": "x"}, {"date": "04/03/2026", "text": "ok"}]
    assert [e["raw_status"] for e in build_history(rows, "en", "FR")] == ["ok"]
    assert build_history(None, "en", "FR") == []


def test_tracking_url_needs_code_and_postcode():
    url = tracking_url("TEST00000001", POSTCODE, "nl")
    assert url.endswith("?numColis=TEST000000011000&lang=nl")
    assert tracking_url(None, POSTCODE, "nl") is None
    assert tracking_url("TEST00000001", "", "nl") is None


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def _normalize(raw, **kwargs):
    kwargs.setdefault("country", "BE")
    kwargs.setdefault("postal_code", POSTCODE)
    return normalize_parcel(raw, **kwargs)


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(_normalize(active_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    active = _normalize(active_sample(), include_history=True)
    if "weight" in CAPABILITIES:
        assert active["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert active["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert active["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert active["url"] is not None
    if "history" in CAPABILITIES:
        assert active["history"] is not None


def test_normalize_active_parcel():
    parcel = _normalize(active_sample())

    assert parcel["carrier"] == "Colis Privé"
    assert parcel["barcode"] == "TEST00000001"
    assert parcel["sender"] == "EXAMPLE SHOP"
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["raw_status"] == SENTENCES["en"]["out_for_delivery"]
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["url"].endswith("numColis=TEST000000011000&lang=en")


def test_fields_the_page_does_not_carry_are_none():
    parcel = _normalize(active_sample())
    for key in ("receiver", "planned_from", "planned_to", "pickup_point", "weight", "dimensions"):
        assert parcel[key] is None
    assert parcel["pickup"] is False


def test_history_is_opt_in():
    assert _normalize(active_sample())["history"] is None
    history = _normalize(active_sample(), include_history=True)["history"]
    assert len(history) == 4
    assert history[0]["raw_status"] == SENTENCES["en"]["registered"]


def test_normalize_uses_the_language_the_payload_was_fetched_in():
    fr = _normalize(active_sample(lang="fr"))
    assert fr["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert fr["raw_status"] == SENTENCES["fr"]["out_for_delivery"]
    assert fr["url"].endswith("lang=fr")


def test_normalize_current_sentence_unmapped_uses_history():
    raw = parsed(status="Something new")
    raw["history"] = [{"date": "05/03/2026", "text": "Something new"}, *raw["history"]]
    assert _normalize(raw)["status"] == ParcelStatus.OUT_FOR_DELIVERY


def test_in_transit_parcel():
    assert _normalize(in_transit_sample())["status"] == ParcelStatus.IN_TRANSIT


def test_normalize_pending_placeholder():
    """A not-found code shows as a silent unknown placeholder."""
    parcel = _normalize({"trackingNumber": "TEST00000009"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["raw_status"] is None
    assert parcel["barcode"] == "TEST00000009"
    assert parcel["url"].endswith("lang=en")


def test_normalize_without_postcode_has_no_url():
    assert normalize_parcel(active_sample())["url"] is None


def test_delivered_comes_only_from_a_mapped_delivered_sentence():
    """An unrecognised final sentence never marks a parcel delivered."""
    sample = delivered_sample()
    sample["statusText"] = "A final sentence nobody has seen"
    sample["history"][0]["text"] = sample["statusText"]
    parcel = _normalize(sample)
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_delivered_parcel_stamps_delivered_at_from_newest_row():
    parcel = _normalize(delivered_sample())
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-03-05T00:00:00+01:00"
    assert parcel["raw_status"] == DELIVERED_TEXT


def test_normalize_keeps_parsed_fields_in_raw():
    raw = active_sample()
    assert _normalize(raw)["raw"] == raw
    assert set(raw) == {"sender", "statusText", "history", "npai", "trackingNumber", "lang"}


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


def test_blank_history_sentences_are_skipped_in_the_fallback():
    status = map_parcel_status("Mystery", "en", ["", None, SENTENCES["en"]["registered"]])
    assert status == ParcelStatus.REGISTERED


@pytest.mark.parametrize("lang", ["fr", "nl", "en"])
def test_letterbox_delivery_maps_to_delivered_in_every_language(lang):
    assert map_parcel_status(SENTENCES[lang]["delivered"], lang) == ParcelStatus.DELIVERED


def test_history_fallback_takes_the_furthest_stage_not_the_top_row():
    texts = SENTENCES["fr"]
    status = map_parcel_status(
        "Une phrase inconnue", "fr", [texts["taken_over"], texts["out_for_delivery"]]
    )
    assert status == ParcelStatus.OUT_FOR_DELIVERY
