"""Backtest job manager — port of `execution/backtestRunner.ts`.

Runs the Python backtester (`services/data/backtester.py --save-db`) as a child
process so the dashboard can kick off a run without the CLI. One run at a time;
progress/result is exposed via :func:`get_job_status` for the UI to poll. The
child persists its own `BacktestRun` row — we only parse its stdout for the id.

Phase 4 change: `asyncio.create_subprocess_exec` instead of Node's `spawn`, so
the job lives in the same event loop as the rest of the backend.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ...core.serialization import iso
from ...core.settings import get_settings
from ...jobs.clock import utcnow

log = logging.getLogger("backend.backtest")

TAIL_MAX = 2_000
_RUN_ID_RE = re.compile(r"id=([a-f0-9]{24,32})")

DEFAULTS: dict[str, Any] = {
    "timeframes": ["15min", "60min", "1min"],
    "symbols": ["XAUUSD", "EURUSD", "BTCUSD"],
    "strategies": ["ict_confluence", "ict_sweep_mss", "ict_order_block", "ict_fvg"],
    "balance": 10_000,
    "risk": 1,
}


@dataclass
class JobStatus:
    running: bool = False
    startedAt: str | None = None
    finishedAt: str | None = None
    exitCode: int | None = None
    ok: bool | None = None
    runId: str | None = None
    error: str | None = None
    #: Last chunk of child output, for surfacing failures in the UI.
    tail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_job = JobStatus()
_task: asyncio.Task[None] | None = None


def data_dir() -> Path:
    """Where `services/data` lives, relative to this backend (env-overridable)."""
    configured = get_settings().backtest_data_dir
    if configured:
        return Path(configured)
    # services/backend/app/domain/execution/ → repo root is parents[4]
    return Path(__file__).resolve().parents[4] / "data"


def python_bin(directory: Path) -> str:
    configured = get_settings().backtest_python
    if configured:
        return configured
    venv = directory / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return shutil.which("python3") or "python3"


def build_args(opts: dict[str, Any]) -> list[str]:
    tfs = opts.get("timeframes") or DEFAULTS["timeframes"]
    syms = opts.get("symbols") or DEFAULTS["symbols"]
    strats = opts.get("strategies") or DEFAULTS["strategies"]
    args = [
        "backtester.py",
        "--save-db",
        "--balance",
        str(opts.get("balance") or DEFAULTS["balance"]),
        "--risk",
        str(opts.get("risk") or DEFAULTS["risk"]),
        "--timeframes",
        *tfs,
        "--symbols",
        *syms,
        "--strategies",
        *strats,
    ]
    if opts.get("noCosts"):
        args.append("--no-costs")
    if opts.get("label"):
        args += ["--label", str(opts["label"])]
    return args


def get_job_status() -> dict[str, Any]:
    return _job.as_dict()


async def _run(py: str, args: list[str], directory: Path) -> None:
    """Drive one child process, streaming its output into the job tail.

    Mutates the module-level `_job` in place rather than rebinding it, so a
    concurrent `get_job_status()` always sees a consistent object.
    """
    timeout_s = get_settings().backtest_timeout_s
    try:
        # argv is assembled by `build_args` from schema-validated enum values —
        # never from raw request text — and no shell is involved.
        process = await asyncio.create_subprocess_exec(
            py,
            *args,
            cwd=str(directory),
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        _job.running = False
        _job.finishedAt = iso(utcnow())
        _job.ok = False
        _job.error = f"failed to start: {exc}"
        return

    buf = ""

    async def drain() -> None:
        nonlocal buf
        stdout = process.stdout
        if stdout is None:  # PIPE was requested, so this cannot happen — be safe anyway
            return
        while True:
            chunk = await stdout.read(4096)
            if not chunk:
                break
            buf = (buf + chunk.decode("utf-8", "replace"))[-TAIL_MAX:]
            _job.tail = buf

    try:
        async with asyncio.timeout(timeout_s):
            await asyncio.gather(drain(), process.wait())
    except TimeoutError:
        _job.error = f"timed out after {timeout_s:.0f}s"
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()

    code = process.returncode
    match = _RUN_ID_RE.search(buf)
    _job.running = False
    _job.finishedAt = iso(utcnow())
    _job.exitCode = code
    _job.ok = code == 0
    _job.runId = match.group(1) if match else None
    if code != 0 and not _job.error:
        _job.error = f"backtester exited with code {code}"
    log.info(
        "[backtestRunner] finished code=%s runId=%s%s",
        code,
        _job.runId or "?",
        f' error="{_job.error}"' if _job.error else "",
    )


def start_backtest(opts: dict[str, Any]) -> bool:
    """Kick off a run. Returns False if one is already in flight (caller 409s)."""
    global _job, _task
    if _job.running:
        return False

    directory = data_dir()
    script = directory / "backtester.py"
    if not script.exists():
        stamp = iso(utcnow())
        _job = JobStatus(
            running=False,
            startedAt=stamp,
            finishedAt=stamp,
            ok=False,
            error=f"backtester.py not found at {script}",
        )
        return True  # job recorded (as failed); not a concurrency rejection

    py = python_bin(directory)
    args = build_args(opts)
    _job = JobStatus(running=True, startedAt=iso(utcnow()))
    _task = asyncio.get_running_loop().create_task(_run(py, args, directory))
    log.info("[backtestRunner] started: %s %s", py, " ".join(args))
    return True
