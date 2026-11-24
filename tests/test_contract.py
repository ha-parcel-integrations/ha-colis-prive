"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.colis_prive import parcels
from custom_components.colis_prive.const import ParcelStatus
from custom_components.colis_prive.parcels import normalize_parcel, parse_iso

from .payloads import active_sample, delivered_sample, in_transit_sample, parsed

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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

# Invented sentence: no real pickup-point sentence has been observed yet.
PICKUP_SENTENCE = "Your parcel is ready at the invented pickup point"


@pytest.fixture
def pickup_mapping(monkeypatch):
    table = (
        (
            parcels._fold("collected from the invented pickup point"),
            ParcelStatus.DELIVERED,
        ),
        (parcels._fold(PICKUP_SENTENCE), ParcelStatus.AT_PICKUP_POINT),
        *parcels._TABLES["en"],
    )
    monkeypatch.setitem(parcels._TABLES, "en", table)


def _pickup_history(*, collected: bool) -> dict:
    sample = in_transit_sample()
    rows = [
        {"date": "05/03/2026", "text": PICKUP_SENTENCE},
        *sample["history"],
    ]
    if collected:
        rows.insert(
            0,
            {
                "date": "06/03/2026",
                "text": "Parcel collected from the invented pickup point",
            },
        )
    sample["history"] = rows
    sample["statusText"] = rows[0]["text"]
    return sample


def _moment_sample() -> dict:
    return active_sample()


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "in_transit": in_transit_sample,
    "bare": lambda: {"trackingNumber": "TEST00000009", "lang": "en"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param](), country="FR")


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done(parcel):
    if parcel["status"] is ParcelStatus.DELIVERED:
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_the_delivered_parcel_has_a_delivered_at():
    parcel = normalize_parcel(delivered_sample(), country="FR")
    assert parcel["delivered_at"] == "2026-03-05T00:00:00+01:00"


def test_the_out_for_delivery_day_is_a_whole_local_day():
    parcel = normalize_parcel(_moment_sample(), country="FR")
    start = datetime.fromisoformat(parcel["planned_from"])
    end = datetime.fromisoformat(parcel["planned_to"])
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
    assert start.utcoffset() == end.utcoffset()


def test_a_parcel_that_reached_a_pickup_point_is_pickup(pickup_mapping):
    parcel = normalize_parcel(_pickup_history(collected=False), country="FR")
    assert parcel["status"] is ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup(pickup_mapping):
    parcel = normalize_parcel(_pickup_history(collected=True), country="FR")
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-03-06T00:00:00+01:00"
    assert parcel["pickup"] is True


def test_a_home_delivery_is_not_pickup():
    sample = parsed(stages=["registered", "taken_over", "arrived", "out_for_delivery"])
    assert normalize_parcel(sample, country="FR")["pickup"] is False


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "2099-04-29T13:00:00+02:00",
            None,
            ("2099-04-29T13:00:00+02:00", "2099-04-29T23:59:59+02:00"),
        ),
        (
            "2099-04-29T13:00:00+02:00",
            "2099-04-29T13:00:00+02:00",
            ("2099-04-29T13:00:00+02:00", "2099-04-29T23:59:59+02:00"),
        ),
        (
            "2099-04-30",
            "2099-04-29",
            ("2099-04-29T00:00:00+02:00", "2099-04-30T23:59:59+02:00"),
        ),
        ("2099-04-29T23:59:59+02:00", None, ("2099-04-29T23:59:59+02:00", None)),
        (
            None,
            "2099-04-29T10:00:00+02:00",
            ("2099-04-29T10:00:00+02:00", "2099-04-29T23:59:59+02:00"),
        ),
        ("not a date", None, (None, None)),
        (None, None, (None, None)),
        (12, None, (None, None)),
    ],
)
def test_planned_window_shapes(start, end, expected):
    zone = ZoneInfo("Europe/Paris")
    assert parcels.planned_window(start, end, zone=zone) == expected


def test_a_naive_time_gets_the_zone():
    zone = ZoneInfo("Europe/Paris")
    assert parcels.planned_window("2099-04-29T10:00:00", None, zone=zone) == (
        "2099-04-29T10:00:00+02:00",
        "2099-04-29T23:59:59+02:00",
    )
