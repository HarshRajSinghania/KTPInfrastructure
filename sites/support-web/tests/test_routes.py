"""Route tests.

These exist because the gating claims are only worth anything end to end: the
disclosure test reads the actual rendered HTML a logged-out visitor receives,
not the section list that produced it.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient


class _FakeConn:
    """Stands in for a pymysql connection; records that it was closed."""

    def __init__(self, log):
        self.log = log

    def close(self):
        self.log.append(("closed",))


@pytest.fixture
def client(tmp_path, monkeypatch):
    pub = tmp_path / "public.json"
    pub.write_text(json.dumps({
        "generated": 2_000_000_000,
        "servers": [{"region": "Dallas", "label": "Dallas 1", "up": True,
                     "connect": "74.91.126.55:27015",
                     "map": "dod_donner", "players": 0, "max_players": 12, "hltv": True}],
        "summary": {"up": 1, "total": 1, "players": 0},
    }))
    det = tmp_path / "detail.json"
    det.write_text(json.dumps({"servers": [{"address": "74.91.126.55:27015"}]}))

    monkeypatch.setenv("SUPPORT_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("SUPPORT_REPORT_SALT", "test-salt")
    monkeypatch.setenv("SUPPORT_PUBLIC_JSON", str(pub))
    monkeypatch.setenv("SUPPORT_DETAIL_JSON", str(det))
    monkeypatch.setenv("SUPPORT_KTP_ADMIN_IDS", "111")
    monkeypatch.setenv("SUPPORT_ONE3_ADMIN_IDS", "333")

    import app.config
    importlib.reload(app.config)
    import app.main
    m = importlib.reload(app.main)

    # No real MySQL and no real relay in tests. Without this the report tests
    # spend ~20s waiting on connection timeouts and, worse, would post to a live
    # Discord channel the moment someone ran them with a configured env.
    sent = []
    monkeypatch.setattr(m.store, "connect", lambda **kw: _FakeConn(sent))
    monkeypatch.setattr(m.store, "insert_report",
                        lambda conn, iid, rep, ip: sent.append(("row", iid, rep.channel.value)) or 1)
    monkeypatch.setattr(m.store, "mark_relayed",
                        lambda conn, rid: sent.append(("marked", rid)))
    monkeypatch.setattr(m.store, "insert_ticket",
                        lambda *a, **k: sent.append(("ticket",)) or 7)
    monkeypatch.setattr(m.store, "set_ticket_status", lambda *a, **k: True)
    monkeypatch.setattr(m.relay, "post_embed",
                        lambda url, sec, ch, emb, **kw: sent.append(("relay", ch, emb))
                        or m.relay.RelayResult(True, 200))
    m.delivered = sent

    def as_tier(did=None):
        from app.tiers import resolve
        m.app.dependency_overrides[m.current_tier] = lambda: resolve(
            did, {"111"}, {"333"}
        )
        return TestClient(m.app)

    yield as_tier
    m.app.dependency_overrides.clear()


# --- what a logged-out visitor actually receives --------------------------

# ".forcereset" is deliberately NOT a discriminator any more: the 1.3 form now carries
# the same RCON warning, because a 1.3 moderator gets the same RCON reach at that level
# and deserves the same sentence about it. "Season captain" replaces it -- a group only
# the KTP privileges form offers.
KTP_ONLY = ("Approval queue", "Request game server privileges", "Bot command hub",
            "/ops fleet-health", "Season captain", "Booking a server",
            "AC review console", "Fleet trends", "Open incidents")
# Now genuinely one3-only. It used to render for KTP too, via an `or` that existed for
# the "Your requests" panel sharing the row -- so KTP admins saw a form that is a strict
# subset of their own (the privileges form's Group dropdown already covers 1.3 admin).
ONE3_ONLY = ("Request moderator access",)


def test_logged_out_receives_no_admin_markup_at_all(client):
    """The claim the whole tier design rests on: gating is server-side, so a
    logged-out response contains nothing to find in view-source."""
    html = client().get("/").text
    for gated in KTP_ONLY + ONE3_ONLY:
        assert gated not in html, f"logged-out HTML leaked {gated!r}"
    assert "Sign in with Discord" in html


def test_one3_gets_its_form_and_none_of_the_ktp_surface(client):
    html = client("333").get("/").text
    assert "Request moderator access" in html
    for ktp_only in KTP_ONLY:
        assert ktp_only not in html, f"1.3 tier leaked {ktp_only!r}"


def test_ktp_gets_its_own_surface_and_not_the_1_3_form(client):
    """KTP sees every KTP section and NOT the 1.3 moderator form.

    The 1.3 form is one fixed cell -- may_request() permits that tier exactly
    ONE3_ADMIN at KICK_RESTART -- and the privileges form's Group dropdown already
    expresses it. Rendering both put two routes to the same ticket on one page.
    """
    html = client("111").get("/").text
    for section in KTP_ONLY:
        assert section in html, f"KTP tier missing {section!r}"
    for one3_only in ONE3_ONLY:
        assert one3_only not in html, f"KTP tier still shows the 1.3-only {one3_only!r}"


def test_both_admin_tiers_get_the_requests_panel(client):
    """"Your requests" is shared, and splitting the row is what could have dropped it
    from one tier -- the `or` it used to ride on lived on the 1.3 form's condition."""
    for discord_id in ("111", "333"):
        assert "Your requests" in client(discord_id).get("/").text,             f"{discord_id} lost the requests panel"


def test_public_page_renders_the_real_sections(client):
    html = client().get("/").text
    for expected in ("Report a problem", "Server commands", "Everything KTP",
                     "Sponsor KTP", "Sign-in", "sign-in"):
        if expected.lower() in html.lower():
            continue
        raise AssertionError(f"public page missing {expected!r}")


def test_status_renders_live_fleet_data(client):
    html = client().get("/").text
    assert "Dallas 1" in html
    assert "0/12" in html
    assert "connect 74.91.126.55:27015" in html
    # The fleet row is label + count + connect. Map name belongs to the live-match
    # strip, and this fixture server has no match state, so it must NOT appear.
    assert "dod_donner" not in html
    assert "No live matches" in html


def test_public_status_api_carries_connect_but_no_internal_detail(client):
    body = client().get("/api/status").text
    assert "74.91.126.55:27015" in body          # the game endpoint, deliberately
    for internal in ("8087", ".service", "consecutive", "last_ok"):
        assert internal not in body


# --- detail.json is gated -------------------------------------------------

@pytest.mark.parametrize("who", [None, "333"])
def test_detail_is_404_not_403_for_non_ktp(client, who):
    # 403 would confirm the endpoint exists; 404 says nothing.
    r = client(who).get("/api/status/detail")
    assert r.status_code == 404
    assert "74.91.126.55" not in r.text


def test_detail_reaches_ktp_admins(client):
    r = client("111").get("/api/status/detail")
    assert r.status_code == 200 and "74.91.126.55:27015" in r.text


def test_healthz_says_nothing_about_the_fleet(client):
    body = client().get("/healthz").json()
    assert body == {"ok": True}


# --- report intake --------------------------------------------------------

def form(**kw):
    d = {"category": "lag", "body": "choke spikes every round", "started": "12"}
    d.update(kw)
    return d


def test_a_valid_report_is_accepted(client):
    r = client().post("/api/report", data=form())
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(r.json()["intake"]) == 12


def test_honeypot_looks_identical_to_success(client):
    r = client().post("/api/report", data=form(website="http://spam"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "intake" not in r.json()      # nothing was actually filed


def test_bad_category_and_empty_body_are_400(client):
    c = client()
    assert c.post("/api/report", data=form(category="nope")).status_code == 400
    assert c.post("/api/report", data=form(body="   ")).status_code == 400


def test_unknown_server_label_is_rejected(client):
    r = client().post("/api/report", data=form(server_label="<script>x</script>"))
    assert r.status_code == 400


def test_known_server_label_is_accepted(client):
    assert client().post("/api/report", data=form(server_label="Dallas 1")).status_code == 200


def test_rate_limit_kicks_in_and_reports_retry_after(client):
    c = client()
    codes = [c.post("/api/report", data=form()).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
    assert int(c.post("/api/report", data=form()).headers["Retry-After"]) > 0


def test_instant_submission_is_rejected(client):
    assert client().post("/api/report", data=form(started="0.2")).status_code == 400


# --- delivery ordering ----------------------------------------------------

def test_report_is_stored_before_it_is_relayed_then_marked(client):
    import app.main as m
    c = client()
    assert c.post("/api/report", data=form()).status_code == 200
    kinds = [e[0] for e in m.delivered]
    assert kinds == ["row", "relay", "marked", "closed"], kinds


def test_cheating_report_relays_to_the_player_channel(client, monkeypatch):
    import app.main as m
    monkeypatch.setenv("SUPPORT_CHANNEL_PLAYER_REPORTS", "200")
    c = client()
    c.post("/api/report", data=form(category="cheating"))
    row = next(e for e in m.delivered if e[0] == "row")
    assert row[2] == "player"


def test_a_relay_failure_still_tells_the_reporter_it_was_sent(client, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.store, "insert_ticket",
                        lambda *a, **k: sent.append(("ticket",)) or 7)
    monkeypatch.setattr(m.store, "set_ticket_status", lambda *a, **k: True)
    monkeypatch.setattr(m.relay, "post_embed",
                        lambda *a, **k: m.relay.RelayResult(False, 502, "bad gateway"))
    r = client().post("/api/report", data=form())
    # The row is durable and the unrelayed index is the retry queue; telling
    # them it failed would make them re-submit something we already have.
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "marked" not in [e[0] for e in m.delivered]


def test_a_database_failure_does_not_block_the_relay(client, monkeypatch):
    import app.main as m

    def boom(**kw):
        raise RuntimeError("mysql is down")

    monkeypatch.setattr(m.store, "connect", boom)
    r = client().post("/api/report", data=form())
    assert r.status_code == 200
    assert "relay" in [e[0] for e in m.delivered]   # report still reached Discord


# --- tickets --------------------------------------------------------------

def ticket_form(**kw):
    d = {"level": "cl", "group": "one3_admin",
         "steam_id": "STEAM_0:1:12345", "display_name": "someone"}
    d.update(kw)
    return d


def test_public_cannot_see_or_file_tickets(client):
    r = client().post("/api/tickets", data=ticket_form())
    assert r.status_code == 404          # 404, not 403 -- says nothing


def test_one3_admin_may_request_only_its_own_group_at_the_lower_level(client):
    c = client("333")
    assert c.post("/api/tickets", data=ticket_form()).status_code == 200
    # The KTP form is never rendered for them, but the endpoint is still open.
    assert c.post("/api/tickets", data=ticket_form(group="ktp_admin")).status_code == 403
    assert c.post("/api/tickets", data=ticket_form(group="season_captain")).status_code == 403
    # Ban is a KTP decision even within their own group.
    assert c.post("/api/tickets", data=ticket_form(level="cdl")).status_code == 403


def test_ktp_admin_may_request_every_combination(client):
    c = client("111")
    for level in ("cl", "cdl"):
        for group in ("ktp_admin", "one3_admin", "season_captain"):
            r = c.post("/api/tickets", data=ticket_form(level=level, group=group))
            assert r.status_code == 200, (level, group, r.text)


def test_unknown_level_or_group_is_rejected(client):
    c = client("111")
    assert c.post("/api/tickets", data=ticket_form(level="abcdefg")).status_code == 400
    assert c.post("/api/tickets", data=ticket_form(group="root")).status_code == 400


@pytest.mark.parametrize("bad", ["", "76561198000000000", "STEAM_0:2:1", "STEAM_0:1:", "nonsense"])
def test_malformed_steamids_are_rejected(client, bad):
    r = client("111").post("/api/tickets", data=ticket_form(steam_id=bad))
    assert r.status_code == 400


def test_steamid_universe_digit_is_normalised(client):
    # STEAM_0:1:5 and STEAM_1:1:5 are the same account; storing both would
    # create two rows for one person and hide an existing grant.
    import app.main as m
    assert m.normalise_steamid("STEAM_1:1:5") == "STEAM_0:1:5"
    assert m.normalise_steamid("steam_0:1:5") == "STEAM_0:1:5"
    assert m.normalise_steamid("  STEAM_0:1:5  ") == "STEAM_0:1:5"


def test_only_ktp_admins_may_advance_a_ticket(client):
    data = {"current": "submitted", "target": "approved"}
    assert client().post("/api/tickets/1/status", data=data).status_code == 404
    assert client("333").post("/api/tickets/1/status", data=data).status_code == 404


def test_a_ticket_cannot_skip_approval(client):
    r = client("111").post("/api/tickets/1/status",
                           data={"current": "submitted", "target": "applied"})
    assert r.status_code == 409
    assert "not allowed" in r.json()["error"]


def test_a_lost_race_reports_conflict_rather_than_success(client, monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.store, "set_ticket_status", lambda *a, **k: False)
    r = client("111").post("/api/tickets/1/status",
                           data={"current": "submitted", "target": "approved"})
    assert r.status_code == 409 and "already moved" in r.json()["error"]


def test_missing_display_name_gets_our_message_not_a_schema_error(client):
    r = client("111").post("/api/tickets", data=ticket_form(display_name="  "))
    assert r.status_code == 400 and r.json()["error"] == "Who is it for?"


# --- the form must actually be able to reach the endpoint -----------------

def test_report_form_posts_to_the_endpoint_with_the_names_it_reads(client):
    """This shipped broken once: the form was carried over from the design
    prototype as `onsubmit="return false"` with ids and no names, so the API
    worked while the page could not reach it. Direct-POST tests do not catch
    that -- only reading the rendered form does."""
    import re
    html = client().get("/").text
    seg = html[html.index('<section id="report"'):]
    seg = seg[:seg.index("</section>")]

    form = re.search(r"<form[^>]*>", seg).group(0)
    assert 'action="/api/report"' in form and 'method="post"' in form

    names = set(re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"', seg))
    # Every parameter api_report() declares must be present in the markup.
    assert {"category", "body", "server_label", "handle", "website", "started"} <= names


def test_form_category_values_match_the_enum_exactly(client):
    """A label change that edits the option value silently breaks submission --
    the endpoint 400s on an unknown category."""
    import re
    from app.reports import Category
    html = client().get("/").text
    seg = html[html.index('<section id="report"'):]
    seg = seg[:seg.index("</section>")]
    offered = set(re.findall(r'<option value="([a-z_]+)"', seg))
    assert offered == {c.value for c in Category}


def test_every_offered_category_is_actually_accepted(client):
    import re
    c = client()
    html = c.get("/").text
    seg = html[html.index('<section id="report"'):]
    offered = set(re.findall(r'<option value="([a-z_]+)"', seg[:seg.index("</section>")]))
    for cat in sorted(offered)[:3]:            # rate limit is 3/hour
        r = c.post("/api/report", data=form(category=cat))
        assert r.status_code == 200, (cat, r.text)


# --- fleet trends: KTP-only, and the page survives its data source ---------

def test_trends_render_for_ktp_from_the_daily_rollup(client, monkeypatch):
    import app.main as m
    from datetime import date
    monkeypatch.setattr(m.store, "telemetry_days", lambda conn, days=30: [
        {"server_endpoint": "74.91.126.55:27015", "day": date(2026, 9, d),
         "fps_p50_today": 999.0 - d, "spike_total_today": d, "warn_fps": int(d == 3),
         "warn_spikes": 0} for d in (1, 2, 3)])
    html = client("111").get("/").text
    assert "Fleet trends" in html
    assert "<polyline points=" in html          # Dallas 1 has a series
    assert "no data" in html                    # every other fleet instance has none
    assert "aria-label=\"Dallas 1 p50 FPS over 3 days\"" in html


def test_trends_query_failure_costs_one_panel_not_the_page(client):
    """The fixture's fake connection has no cursor, so the real query raises.
    That must render as unavailable, not as a 500 and not as an empty fleet."""
    r = client("111").get("/")
    assert r.status_code == 200
    assert "Trend data could not be read" in r.text
    assert "Fleet trends" in r.text
    assert "<polyline" not in r.text


def test_public_page_opens_no_database_connection_for_trends(client, monkeypatch):
    import app.main as m
    opened = []
    monkeypatch.setattr(m.store, "connect", lambda **kw: opened.append(kw) or (_ for _ in ()).throw(RuntimeError("no")))
    assert client().get("/").status_code == 200
    assert opened == []


# --- open incidents: the health check's state file, KTP-only -----------------

def test_incidents_render_for_ktp_from_the_health_state_file(client, monkeypatch, tmp_path):
    import app.main as m
    state = tmp_path / "health.json"
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.write_text(json.dumps({
        "updated_at": now,
        "down": ["failed-unit:ktp-identity-reconcile.service"],
        "since": {"failed-unit:ktp-identity-reconcile.service": "2026-09-08 09:01:53"},
        "detail": {},
    }))
    import dataclasses
    monkeypatch.setattr(m, "settings", dataclasses.replace(m.settings, health_state=str(state)))
    html = client("111").get("/").text
    assert "Open incidents" in html
    assert "failed-unit:ktp-identity-reconcile.service" in html
    assert "2026-09-08 09:01:53" in html


def test_incidents_missing_state_file_is_unavailable_not_a_500(client, monkeypatch, tmp_path):
    import app.main as m
    import dataclasses
    monkeypatch.setattr(m, "settings", dataclasses.replace(m.settings, health_state=str(tmp_path / "absent.json")))
    r = client("111").get("/")
    assert r.status_code == 200
    assert "incident state is unavailable" in r.text
