"""Settings tests.

The empty-string cases are the point: deployment tooling sets a variable to ''
when it has no value, and os.environ.get(k, default) returns '' rather than the
default in that case. That has already pinned a run to the empty string once in
this repo.
"""

import importlib

import pytest


@pytest.fixture
def cfg(monkeypatch):
    def build(**env):
        for k in list(env):
            monkeypatch.setenv(k, env[k])
        import app.config as c
        return importlib.reload(c)
    return build


def test_set_but_empty_falls_back_to_the_default(cfg, monkeypatch):
    monkeypatch.setenv("SUPPORT_PUBLIC_JSON", "")
    c = cfg()
    assert c.settings.public_json.endswith("public.json")
    assert c.settings.public_json != ""


def test_default_does_not_point_at_the_retired_docroot(cfg, monkeypatch):
    monkeypatch.setenv("SUPPORT_PUBLIC_JSON", "")
    c = cfg()
    assert "support.ktpdod.com" not in c.settings.public_json


def test_a_real_value_wins(cfg):
    c = cfg(SUPPORT_PUBLIC_JSON="/tmp/x.json")
    assert c.settings.public_json == "/tmp/x.json"


def test_admin_ids_parse_and_tolerate_whitespace_and_blanks(cfg):
    c = cfg(SUPPORT_KTP_ADMIN_IDS=" 111 , 222 ,, 333 ", SUPPORT_ONE3_ADMIN_IDS="")
    assert c.settings.ktp_admin_ids == {"111", "222", "333"}
    assert c.settings.one3_admin_ids == set()


def test_configured_flags_are_false_until_both_halves_are_present(cfg):
    c = cfg(DISCORD_CLIENT_ID="abc", DISCORD_CLIENT_SECRET="")
    assert not c.settings.oauth_configured
    c = cfg(DISCORD_CLIENT_ID="abc", DISCORD_CLIENT_SECRET="def")
    assert c.settings.oauth_configured


def test_channel_routing_uses_the_derived_name(cfg):
    c = cfg(SUPPORT_CHANNEL_SERVER_REPORTS="100", SUPPORT_CHANNEL_PLAYER_REPORTS="200")
    assert c.settings.channel_for("player") == "200"
    assert c.settings.channel_for("server") == "100"
    # Anything unexpected goes to ops, never to the narrower player channel.
    assert c.settings.channel_for("nonsense") == "100"
