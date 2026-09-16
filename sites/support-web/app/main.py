"""support.ktpdod.com — one page, rendered per session by tier.

Route surface is small on purpose. Everything with real logic lives in a pure
module (tiers, status, reports, tickets, season) and is tested without a
request; this file is wiring.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import date

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import relay, status as st, store, trends as tr
from .config import settings
from .poller import fleet
from .reports import RateLimiter, ReportRejected, validate
from .season import current_season
from .tickets import Group, Level, Status, TransitionError, may_request, transition
from .tiers import Tier, can_view_detail_status, resolve, visible_sections

log = logging.getLogger("support-web")

app = FastAPI(title="KTP Support", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, https_only=True)
templates = Jinja2Templates(directory="app/templates")

limiter = RateLimiter()

oauth = OAuth()
oauth.register(
    name="discord",
    client_id=settings.discord_client_id,
    client_secret=settings.discord_client_secret,
    access_token_url="https://discord.com/api/oauth2/token",
    authorize_url="https://discord.com/api/oauth2/authorize",
    api_base_url="https://discord.com/api/",
    # `identify` only. Guild-role gating would need guilds.members.read plus a
    # bot token on an internet-facing app; see design/DESIGN.md.
    client_kwargs={"scope": "identify", "token_endpoint_auth_method": "client_secret_post"},
)

SESSION_ID, SESSION_NAME = "discord_id", "discord_name"

# Both universes appear in the wild and STEAM_0:Y:Z and STEAM_1:Y:Z are the same
# account. Storing whichever the requester happened to paste would create two
# rows for one person and hide an existing grant.
_STEAMID = re.compile(r"^STEAM_[0-9]:([01]):(\d+)$", re.I)


def normalise_steamid(raw: str) -> str | None:
    m = _STEAMID.match((raw or "").strip())
    return f"STEAM_0:{m.group(1)}:{m.group(2)}" if m else None


def current_tier(request: Request) -> Tier:
    return resolve(
        request.session.get(SESSION_ID), settings.ktp_admin_ids, settings.one3_admin_ids
    )


def client_ip(request: Request) -> str:
    """Left-most X-Forwarded-For entry, since nginx sits in front.

    Trusted because only our own proxy can reach the app port; if this is ever
    exposed directly, this becomes attacker-controlled and the rate limiter
    stops working.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "0.0.0.0")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, tier: Tier = Depends(current_tier)):
    doc = st.load(settings.public_json)
    sections = visible_sections(tier)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tier": tier.value,
            "sections": sections,
            "status": st.view(doc),
            "server_labels": sorted(st.server_labels(doc)),
            "user": request.session.get(SESSION_NAME),
            "trends": load_trends() if "trends" in sections else None,
        },
    )


def load_trends() -> list[dict] | None:
    """The admin trends table, or None when the data cannot be read.

    None renders as "unavailable", never as an empty fleet and never as a 500:
    a grant missing on the database must cost the page one panel, not the page.
    Only fetched for a tier that will render it, so a public request never opens
    a database connection for a section it cannot see.
    """
    try:
        conn = store.connect(**settings.db_kwargs)
    except Exception:
        log.exception("trends: database connect failed")
        return None
    try:
        return tr.shape(store.telemetry_days(conn), fleet())
    except Exception:
        log.exception("trends: query or shaping failed")
        return None
    finally:
        conn.close()


@app.get("/healthz")
def healthz():
    """Liveness only -- deliberately says nothing about the fleet."""
    return {"ok": True}


@app.get("/api/status")
def api_status():
    """Same document the page renders, with the same staleness verdict applied.

    Serving it here rather than letting callers read public.json directly means
    a stale document is reported as stale to them too.
    """
    return JSONResponse(st.view(st.load(settings.public_json)))


@app.get("/api/status/detail")
def api_status_detail(tier: Tier = Depends(current_tier)):
    if not can_view_detail_status(tier):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return JSONResponse(st.load(settings.detail_json) or {})


@app.post("/api/report")
async def api_report(
    request: Request,
    category: str = Form(...),
    body: str = Form(...),
    server_label: str = Form(""),
    handle: str = Form(""),
    website: str = Form(""),          # honeypot; real users never see it
    started: str = Form("0"),
):
    key = RateLimiter.key(client_ip(request), settings.report_salt)
    if not limiter.check(key):
        return JSONResponse(
            {"ok": False, "error": "Too many reports from here. Try again later."},
            status_code=429,
            headers={"Retry-After": str(limiter.retry_after(key))},
        )

    labels = st.server_labels(st.load(settings.public_json))
    try:
        elapsed = max(0.0, float(started or 0))
    except ValueError:
        elapsed = 0.0

    try:
        report = validate(
            category=category,
            body=body,
            server_label=server_label,
            handle=handle,
            honeypot=website,
            elapsed=elapsed,
            valid_labels=labels or None,
        )
    except ReportRejected as exc:
        # The honeypot path returns 200: a bot must not learn it was caught.
        if str(exc) == "dropped":
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    intake_id = secrets.token_hex(6)
    deliver_report(report, intake_id, key)
    # The reporter is told "sent" either way. Delivery is our problem from here:
    # the row is durable and the unrelayed index is the retry queue, so telling
    # them it failed would make them re-submit something we already have.
    return JSONResponse({"ok": True, "intake": intake_id})


def deliver_report(report, intake_id: str, ip_hash: str) -> None:
    """Persist, then relay, then mark. Never raises into the request."""
    conn = report_id = None
    try:
        conn = store.connect(**settings.db_kwargs)
        report_id = store.insert_report(conn, intake_id, report, ip_hash)
    except Exception as exc:                     # noqa: BLE001 - log and continue
        log.error("report %s could not be stored: %s", intake_id, exc)

    result = relay.post_embed(
        settings.relay_url,
        settings.relay_secret,
        settings.channel_for(report.channel.value),
        relay.report_embed(report, intake_id),
    )
    if not result.ok:
        log.error("report %s relay failed (%s): %s", intake_id, result.status, result.error)
    elif conn and report_id:
        try:
            store.mark_relayed(conn, report_id)
        except Exception as exc:                 # noqa: BLE001
            # Worst case it is retried and posts twice, which is recoverable;
            # a lost report is not.
            log.error("report %s relayed but not marked: %s", intake_id, exc)
    if conn:
        conn.close()


@app.post("/api/tickets")
def create_ticket(
    request: Request,
    level: str = Form(...),
    group: str = Form(...),
    # Defaulted rather than required: an empty required Form yields FastAPI's
    # raw 422 schema error, and the person filling this in should see our
    # sentence about SteamID format instead.
    steam_id: str = Form(""),
    display_name: str = Form(""),
    note: str = Form(""),
    tier: Tier = Depends(current_tier),
):
    """File a privilege request. Never touches users.ini -- this is a row."""
    if not tier.is_admin:
        return JSONResponse({"detail": "not found"}, status_code=404)
    try:
        want_level, want_group = Level(level), Group(group)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Unknown privilege."}, status_code=400)

    # Checked again here, not only in the template: a 1.3 admin who never sees
    # the KTP form can still POST to this endpoint.
    if not may_request(tier.value, want_level, want_group):
        return JSONResponse({"ok": False, "error": "Not yours to request."}, status_code=403)

    sid = normalise_steamid(steam_id)
    if not sid:
        return JSONResponse(
            {"ok": False, "error": "SteamID must look like STEAM_0:1:12345."}, status_code=400
        )

    name = display_name.strip()[:64]
    if not name:
        return JSONResponse({"ok": False, "error": "Who is it for?"}, status_code=400)

    season = (current_season(date.today()).number
              if want_group.expires_with_season else None)
    try:
        conn = store.connect(**settings.db_kwargs)
    except Exception as exc:                                     # noqa: BLE001
        log.error("ticket store unavailable: %s", exc)
        return JSONResponse({"ok": False, "error": "Try again shortly."}, status_code=503)
    try:
        ticket_id = store.insert_ticket(
            conn, want_level, want_group, sid, name,
            request.session.get(SESSION_ID, ""), note.strip()[:500] or None, season,
        )
    finally:
        conn.close()
    return JSONResponse({"ok": True, "ticket": ticket_id})


@app.post("/api/tickets/{ticket_id}/status")
def advance_ticket(
    ticket_id: int,
    request: Request,
    current: str = Form(...),
    target: str = Form(...),
    tier: Tier = Depends(current_tier),
):
    """Move a ticket along. KTP admins only -- 1.3 admins request, they do not decide."""
    if tier is not Tier.KTP:
        return JSONResponse({"detail": "not found"}, status_code=404)
    try:
        now_status, want_status = Status(current), Status(target)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Unknown status."}, status_code=400)

    try:
        transition(now_status, want_status)
    except TransitionError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)

    try:
        conn = store.connect(**settings.db_kwargs)
    except Exception as exc:                                     # noqa: BLE001
        log.error("ticket store unavailable: %s", exc)
        return JSONResponse({"ok": False, "error": "Try again shortly."}, status_code=503)
    try:
        moved = store.set_ticket_status(
            conn, now_status, want_status, ticket_id, request.session.get(SESSION_ID, "")
        )
    finally:
        conn.close()

    if not moved:
        # The row was not in `current` any more: someone else already acted.
        # Reporting success would show this admin a decision that is not theirs.
        return JSONResponse(
            {"ok": False, "error": "Someone else already moved this ticket. Reload."},
            status_code=409,
        )
    return JSONResponse({"ok": True, "status": want_status.value})


@app.get("/auth/login")
async def auth_login(request: Request):
    if not settings.oauth_configured:
        return RedirectResponse("/", status_code=303)
    return await oauth.discord.authorize_redirect(request, settings.discord_redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.discord.authorize_access_token(request)
    me = (await oauth.discord.get("users/@me", token=token)).json()
    request.session[SESSION_ID] = str(me.get("id", ""))
    request.session[SESSION_NAME] = me.get("global_name") or me.get("username")
    return RedirectResponse("/", status_code=303)


@app.get("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
