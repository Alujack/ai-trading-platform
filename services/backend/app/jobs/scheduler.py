"""Scheduled jobs — port of `apps/api/src/execution/scheduler.ts`.

Same cadences as the Express node-cron setup, with two additions the plan
requires:

* **Single owner.** Jobs only start when `BACKEND_JOB_OWNER=true`, and each tick
  additionally takes a short Redis lock, so running several uvicorn workers can
  never double-execute a tick.
* **No overlap.** An in-flight tick is skipped rather than queued, exactly like
  the `*Running` guards in the TypeScript version.

Every tick opens its own session and swallows its own exceptions: a failing job
must not take down the scheduler or the API.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.settings import get_settings
from ..db.redis_client import try_lock
from ..db.session import session_scope
from .clock import utcnow

log = logging.getLogger("backend.scheduler")

PAPER_CRON = "*/5 * * * *"
SCALP_CRON = "*/15 * * * * *"  # every 15s — scalps need active management
WEEKLY_CRON = "0 0 * * 0"  # Sunday 00:00 UTC
DAILY_CRON = "0 6 * * *"  # 06:00 UTC — morning briefing before the session
EXPIRY_CRON = "* * * * *"  # every minute — approvals are perishable

_scheduler: AsyncIOScheduler | None = None
_running: set[str] = set()


def _cron(expr: str) -> CronTrigger:
    """Build a UTC trigger from a 5- or 6-field cron expression."""
    parts = expr.split()
    if len(parts) == 6:
        second, minute, hour, day, month, dow = parts
    else:
        second = "0"
        minute, hour, day, month, dow = parts
    return CronTrigger(
        second=second,
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=dow,
        timezone="UTC",
    )


async def _guarded(
    name: str, lock_ttl_s: int, body: Callable[[AsyncSession], Awaitable[None]]
) -> None:
    """Run one tick: skip if already in flight, hold the Redis lock, never raise."""
    if name in _running:
        log.info("[%s] %s prev_run_in_progress, skipping", name, utcnow().isoformat())
        return
    _running.add(name)
    try:
        async with try_lock(f"jobs:lock:{name}", lock_ttl_s) as acquired:
            if not acquired:
                log.debug("[%s] another process holds the lock, skipping", name)
                return
            async with session_scope() as session:
                await body(session)
    except Exception as exc:
        log.error('[%s] %s error="%s"', name, utcnow().isoformat(), exc)
    finally:
        _running.discard(name)


# --------------------------------------------------------------------------- #
# Tick bodies
# --------------------------------------------------------------------------- #


async def _paper_body(session: AsyncSession) -> None:
    from ..domain.execution.live_trade import monitor_live_trades
    from ..domain.execution.paper_trading import monitor_open_trades
    from ..domain.execution.policy import reconcile_pending_signals

    rec = await reconcile_pending_signals(session)
    log.info(
        "[paperCron] %s reconcile scanned=%d opened=%d awaiting=%d held=%d blocked=%d",
        utcnow().isoformat(),
        rec.scanned,
        rec.opened,
        rec.awaiting,
        rec.held,
        rec.blocked,
    )
    if get_settings().is_live_broker:
        mon = await monitor_live_trades(session)
        log.info(
            "[paperCron] %s live_monitor inspected=%d closed=%d unchanged=%d",
            utcnow().isoformat(),
            mon.inspected,
            mon.closed,
            mon.unchanged,
        )
    else:
        mon = await monitor_open_trades(session)
        log.info(
            "[paperCron] %s monitor inspected=%d closed=%d unchanged=%d no_price=%d",
            utcnow().isoformat(),
            mon.inspected,
            mon.closed,
            mon.unchanged,
            mon.noPrice,
        )


async def _scalp_body(session: AsyncSession) -> None:
    from ..domain.execution.scalp_manager import run_scalp_management_tick

    result = await run_scalp_management_tick(session)
    if result.closed > 0:
        log.info(
            "[scalpManager] %s managed=%d closed=%d held=%d gone=%d",
            utcnow().isoformat(),
            result.managed,
            result.closed,
            result.held,
            result.gone,
        )


async def _weekly_body(session: AsyncSession) -> None:
    from ..domain.execution.paper_trading import run_weekly_journal_review

    result = await run_weekly_journal_review(session)
    log.info(
        "[weeklyReviewCron] %s status=%s trades=%s%s",
        utcnow().isoformat(),
        result.get("status"),
        result.get("tradeCount", 0),
        f' reason="{result["reason"]}"' if result.get("reason") else "",
    )


async def _daily_body(session: AsyncSession, notify: bool = True) -> None:
    from ..domain.execution.daily_briefing import run_daily_briefing
    from ..domain.execution.data_freshness import send_data_freshness_alert
    from ..domain.execution.news_brief import send_daily_news_brief

    await run_daily_briefing(session)
    # Push the morning news brief on the scheduled run only — not on the
    # startup-once pass, so restarts don't spam the chat.
    if notify and get_settings().enable_news_brief:
        result = await send_daily_news_brief(session)
        log.info(
            "[newsBrief] %s sent=%s%s",
            utcnow().isoformat(),
            result.get("sent"),
            f' reason={result["reason"]}' if result.get("reason") else "",
        )
    # Daily staleness alert: scheduled runs only, so restarts don't spam the chat.
    if notify:
        fresh = await send_data_freshness_alert(session)
        if fresh.get("stale") or fresh.get("sent"):
            log.info(
                "[dataFreshness] %s stale=%s sent=%s%s",
                utcnow().isoformat(),
                fresh.get("stale"),
                fresh.get("sent"),
                f' reason={fresh["reason"]}' if fresh.get("reason") else "",
            )


async def _expiry_body(session: AsyncSession) -> None:
    from ..domain.execution.review_agent import expire_stale_recommendations
    from ..integrations.telegram.approvals import expire_stale_approvals

    approvals = await expire_stale_approvals(session)
    if approvals["expired"] > 0:
        log.info("[approvalExpiry] %s expired=%d", utcnow().isoformat(), approvals["expired"])
    recs = await expire_stale_recommendations(session)
    if recs["expired"] > 0:
        log.info(
            "[approvalExpiry] %s recommendations_expired=%d",
            utcnow().isoformat(),
            recs["expired"],
        )


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def _tick(name: str, ttl: int, body: Callable[[AsyncSession], Awaitable[None]]):
    async def runner() -> None:
        await _guarded(name, ttl, body)

    return runner


def start_schedulers() -> None:
    """Start the jobs this process owns. No-op unless `BACKEND_JOB_OWNER=true`."""
    global _scheduler
    cfg = get_settings()

    if not cfg.backend_job_owner:
        log.warning("[scheduler] BACKEND_JOB_OWNER=false — no scheduled jobs on this process")
        return
    if cfg.api_shadow_mode:
        # A shadow instance computes decisions but must never act on them.
        log.warning("[scheduler] API_SHADOW_MODE=true — execution jobs stay stopped")
        return
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")

    if cfg.enable_paper_trading:
        _scheduler.add_job(
            _tick("paperCron", 280, _paper_body), _cron(PAPER_CRON), id="paperCron"
        )
        log.info('[paperCron] scheduled "%s"', PAPER_CRON)
        _scheduler.add_job(
            _tick("approvalExpiry", 55, _expiry_body), _cron(EXPIRY_CRON), id="approvalExpiry"
        )
        log.info('[approvalExpiry] scheduled "%s"', EXPIRY_CRON)
        if cfg.is_live_broker and cfg.enable_scalp_manager:
            _scheduler.add_job(
                _tick("scalpManager", 14, _scalp_body), _cron(SCALP_CRON), id="scalpManager"
            )
            log.info('[scalpManager] scheduled "%s"', SCALP_CRON)

    if cfg.enable_weekly_review:
        _scheduler.add_job(
            _tick("weeklyReviewCron", 3600, _weekly_body), _cron(WEEKLY_CRON), id="weeklyReviewCron"
        )
        log.info('[weeklyReviewCron] scheduled "%s"', WEEKLY_CRON)

    if cfg.enable_daily_briefing:
        _scheduler.add_job(
            _tick("dailyBriefingCron", 900, _daily_body), _cron(DAILY_CRON), id="dailyBriefingCron"
        )
        log.info('[dailyBriefingCron] scheduled "%s"', DAILY_CRON)

    _scheduler.start()


def stop_schedulers() -> None:
    """Stop every job. Called on shutdown and before an execution-owner handover."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    log.info("[scheduler] stopped")


def scheduler_state() -> dict[str, object]:
    """What this process is running — surfaced by `/health/ready` for observability."""
    cfg = get_settings()
    jobs = [] if _scheduler is None else [j.id for j in _scheduler.get_jobs()]
    return {
        "jobOwner": cfg.backend_job_owner,
        "shadowMode": cfg.api_shadow_mode,
        "running": sorted(jobs),
        "inFlight": sorted(_running),
    }


# --- on-demand run (startup) ---


async def run_daily_briefing_once() -> None:
    """Recompute the briefing on startup, but don't ping Telegram.

    The only on-demand entry point with a caller (`app/main.py`): a restart gets
    a fresh summary without spamming the chat.
    """

    async def body(session: AsyncSession) -> None:
        await _daily_body(session, notify=False)

    await _guarded("dailyBriefingCron", 900, body)
