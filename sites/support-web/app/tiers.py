"""Who sees what.

Tier resolution is pure and lives apart from the request layer so it can be
tested without a session, a database, or Discord. `main.py` supplies the two
allowlists and the signed-in snowflake; everything below is a function of those.

Deliberately allowlist-based rather than Discord guild roles. lan-web already
proves this pattern in production and it needs only the `identify` scope; guild
roles would add a bot token to an internet-facing app and a second consent
prompt, for about a dozen slow-changing admins. The trade is real and one-way:
an allowlist does NOT follow a Discord role change, so removing someone's admin
in Discord does not remove them here. Revocation is a deliberate act.
"""

from __future__ import annotations

from enum import Enum


class Tier(str, Enum):
    PUBLIC = "public"
    ONE3 = "one3"
    KTP = "ktp"

    @property
    def is_admin(self) -> bool:
        return self is not Tier.PUBLIC


def resolve(
    discord_id: str | None,
    ktp_admins: set[str],
    one3_admins: set[str],
) -> Tier:
    """Highest tier the signed-in account holds.

    KTP wins when an account is in both lists: someone who administers the
    league and the 1.3 community should not lose league tooling because they
    also appear on the smaller list.
    """
    if not discord_id:
        return Tier.PUBLIC
    if discord_id in ktp_admins:
        return Tier.KTP
    if discord_id in one3_admins:
        return Tier.ONE3
    return Tier.PUBLIC


def visible_sections(tier: Tier) -> list[str]:
    """Section keys the server should render for this tier.

    The page is one document rendered per session, so a logged-out visitor's
    HTML contains no admin markup at all -- there is nothing to reveal by
    reading the source. Gating in CSS or JS would ship it to everyone.
    """
    sections = ["status", "report", "hub", "sponsor"]
    if tier is Tier.ONE3:
        sections.append("request_one3")
    if tier is Tier.KTP:
        sections += ["request_ktp", "commands", "console", "tickets", "trends"]
    return sections


def can_view_detail_status(tier: Tier) -> bool:
    """detail.json carries addresses, miss counts and error text."""
    return tier is Tier.KTP
