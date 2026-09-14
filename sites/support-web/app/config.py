"""Environment-driven settings.

`os.environ.get(k, default)` is avoided throughout in favour of `or`: a key set
to the empty string returns '' rather than the default, and deployment tooling
routinely sets a variable to '' when it has no value. That pattern has already
pinned a CI run to the empty string once in this repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _ids(raw: str | None) -> set[str]:
    return {p.strip() for p in (raw or "").split(",") if p.strip()}


@dataclass(frozen=True)
class Settings:
    session_secret: str
    report_salt: str

    discord_client_id: str
    discord_client_secret: str
    discord_redirect_uri: str

    guild_id: str
    channel_server_reports: str
    channel_player_reports: str
    relay_url: str
    relay_secret: str

    ktp_admin_ids: set[str] = field(default_factory=set)
    one3_admin_ids: set[str] = field(default_factory=set)

    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "support_web"
    db_password: str = ""
    db_name: str = "hlstatsx"

    public_json: str = "/var/lib/support-web/public.json"
    detail_json: str = "/var/lib/support-web/detail.json"

    @property
    def oauth_configured(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)

    @property
    def relay_configured(self) -> bool:
        return bool(self.relay_url and self.relay_secret)

    @property
    def db_kwargs(self) -> dict:
        return {"host": self.db_host, "port": self.db_port, "user": self.db_user,
                "password": self.db_password, "database": self.db_name}

    def channel_for(self, channel_name: str) -> str:
        return (
            self.channel_player_reports
            if channel_name == "player"
            else self.channel_server_reports
        )


def load() -> Settings:
    env = os.environ.get
    return Settings(
        session_secret=env("SUPPORT_SESSION_SECRET") or "",
        report_salt=env("SUPPORT_REPORT_SALT") or "",
        discord_client_id=env("DISCORD_CLIENT_ID") or "",
        discord_client_secret=env("DISCORD_CLIENT_SECRET") or "",
        discord_redirect_uri=env("DISCORD_REDIRECT_URI") or "",
        guild_id=env("SUPPORT_DISCORD_GUILD_ID") or "",
        channel_server_reports=env("SUPPORT_CHANNEL_SERVER_REPORTS") or "",
        channel_player_reports=env("SUPPORT_CHANNEL_PLAYER_REPORTS") or "",
        relay_url=env("DISCORD_RELAY_URL") or "",
        relay_secret=env("DISCORD_RELAY_SECRET") or "",
        db_host=env("SUPPORT_DB_HOST") or Settings.db_host,
        db_port=int(env("SUPPORT_DB_PORT") or Settings.db_port),
        db_user=env("SUPPORT_DB_USER") or Settings.db_user,
        db_password=env("SUPPORT_DB_PASSWORD") or "",
        db_name=env("SUPPORT_DB_NAME") or Settings.db_name,
        ktp_admin_ids=_ids(env("SUPPORT_KTP_ADMIN_IDS")),
        one3_admin_ids=_ids(env("SUPPORT_ONE3_ADMIN_IDS")),
        public_json=env("SUPPORT_PUBLIC_JSON") or Settings.public_json,
        detail_json=env("SUPPORT_DETAIL_JSON") or Settings.detail_json,
    )


settings = load()
