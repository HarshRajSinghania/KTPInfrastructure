"""incidents.view: the health check's state file, read with staleness as a state."""

from datetime import datetime

from app.incidents import MISSING, STALE_AFTER, age, view

NOW = datetime(2026, 9, 16, 12, 0, 0)


def test_missing_or_unreadable_document_is_unavailable_not_empty():
    for doc in (None, {}, {"down": ["x"]}, {"updated_at": "not a time", "down": ["x"]}):
        v = view(doc, NOW)
        assert v["freshness"] == "missing" and v["rows"] == [] and v["message"] == MISSING


def test_a_stale_document_drops_its_items_rather_than_showing_them_as_current():
    """The check runs hourly. A document three hours old means the check has
    stopped; its down-set is history, not state."""
    doc = {"updated_at": "2026-09-16 08:59:00", "down": ["mysql.service=failed"],
           "since": {"mysql.service=failed": "2026-09-16 08:00:00"}}
    v = view(doc, NOW)
    assert v["freshness"] == "stale" and v["rows"] == []
    assert v["updated"] == "2026-09-16 08:59:00" and v["age"] == "3h"
    # And one second inside the window still renders.
    doc["updated_at"] = datetime.fromtimestamp(NOW.timestamp() - STALE_AFTER).strftime("%Y-%m-%d %H:%M:%S")
    assert view(doc, NOW)["freshness"] == "fresh"


def test_items_sort_longest_open_first_with_detail_and_age():
    doc = {"updated_at": "2026-09-16 11:17:02",
           "down": ["disk-growth:/", "failed-unit:ktp-identity-reconcile.service"],
           "since": {"disk-growth:/": "2026-09-16 09:17:02",
                     "failed-unit:ktp-identity-reconcile.service": "2026-09-08 09:01:53"},
           "detail": {"disk-growth:/": "4 GiB/day over the last 12h+"}}
    v = view(doc, NOW)
    assert [i["key"] for i in v["rows"]] == [
        "failed-unit:ktp-identity-reconcile.service", "disk-growth:/"]
    assert v["rows"][0]["age"] == "8d 2h" and v["rows"][0]["detail"] == ""
    assert v["rows"][1]["age"] == "2h" and v["rows"][1]["detail"] == "4 GiB/day over the last 12h+"


def test_a_pre_since_state_file_says_unknown_rather_than_inventing_a_start():
    """The shape the health check wrote before it kept `since`."""
    doc = {"updated_at": "2026-09-16 11:17:02", "down": ["disk-growth:/"]}
    v = view(doc, NOW)
    assert v["rows"] == [{"key": "disk-growth:/", "detail": "", "since": None, "age": "unknown"}]


def test_age_units():
    assert age(datetime(2026, 9, 16, 11, 58), NOW) == "2m"
    assert age(datetime(2026, 9, 16, 9, 30), NOW) == "2h"
    assert age(datetime(2026, 9, 14, 12, 0), NOW) == "2d 0h"
    assert age(datetime(2026, 9, 17, 12, 0), NOW) == "0m"     # clock skew never goes negative
