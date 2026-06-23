# Phase 1 Plan — Close the Data Foundation gaps

_Roadmap reference: `docs/trading_roadmap.md` Phase 1 (Week 3–5)._

The foundation is **largely complete**. This plan fills the four real gaps so Phase 1 is truly done before piling more on top.

---

## §1 Convert `Candle` to a TimescaleDB hypertable

**Why:** TimescaleDB is installed and the candle table is the canonical time-series shape (`(symbol, timeframe, timestamp)`). As candle volume grows past ~100k rows, vanilla btree scans on date ranges get slow. A hypertable partitions by `timestamp` automatically.

**Steps**
1. Confirm no active writers during migration (`docker compose stop` the API + data worker, or run during a quiet window).
2. Create a new Prisma migration `apps/api/prisma/migrations/<timestamp>_candle_hypertable/migration.sql`:
   ```sql
   -- Candle table already exists; convert it to a hypertable.
   SELECT create_hypertable('"Candle"', 'timestamp', if_not_exists => TRUE, migrate_data => TRUE);
   -- Tune chunk size to ~1 week for intraday data.
   SELECT set_chunk_time_interval('"Candle"', INTERVAL '7 days');
   ```
3. Same migration: add a compression policy for chunks older than 30 days (optional, free disk wins).
4. Run `npx prisma migrate deploy` and verify with:
   ```sql
   SELECT * FROM timescaledb_information.hypertables;
   ```
5. Re-run a candle query (e.g. `EXPLAIN ANALYZE` on a date-bounded SELECT) — should show chunk pruning.

**Out-of-scope:** doing the same for `Indicator`. It can be added later if it becomes a bottleneck.

**Done when:** `timescaledb_information.hypertables` lists `Candle`, and existing 905 rows are still queryable.

---

## §2 Persist risk decisions to `RiskLog`

**Why:** The `RiskLog` table exists but the risk engine [riskEngine.ts:38-245](apps/api/src/risk/riskEngine.ts#L38-L245) never writes to it. Without persisted decisions, the dashboard can't ever show "why was this trade rejected?" or "what circuit breaker tripped today?"

**Steps**
1. Add a `recordRiskDecision()` helper in `apps/api/src/risk/riskEngine.ts` that takes the relevant fields and writes to the `RiskLog` table via Prisma.
2. Call it from inside `validateTrade()` for both APPROVED and REJECTED outcomes. Capture:
   - `accountBalance`, `riskPercent`, computed `positionSize`
   - `dailyLoss` (sum of today's closed-trade losses) and `dailyLossLimit`
   - `circuitBreakerTripped` boolean
3. Also call it from `calculatePositionSize()` callers where position size matters (paper-trading sweep currently logs to console — should also persist).
4. Add a unit test in `apps/api/src/risk/riskEngine.test.ts` asserting that one `RiskLog` row is created per call.
5. Add a `/api/risk/recent` route returning the last N rows (for a future dashboard widget — defer the UI).

**Done when:** `SELECT COUNT(*) FROM "RiskLog"` is non-zero after one signal cron tick; rejected signals also produce a row with `circuitBreakerTripped = true` where appropriate.

---

## §3 Remove the stub `services/data/src/worker.py`

**Why:** Two files claim to be the data entry point — [services/data/main.py](services/data/main.py) (the real one, with cron loop + indicator hook) and [services/data/src/worker.py](services/data/src/worker.py) (just prints `"data worker starting"`). The README still says `python -m src.worker`, which leads new developers to the dead path.

**Steps**
1. Delete `services/data/src/worker.py` (or move the real `main.py` into `src/` and have `worker.py` re-export it).
2. Update [services/data/README.md](services/data/README.md) `Run` section to use `python main.py` (or whatever the canonical command is).
3. Update root `package.json` if there's a `dev:data` script that points at the stub (there isn't, currently — just confirm).
4. Sanity-check: from a clean shell, `cd services/data && source .venv/bin/activate && python main.py` should start ingestion.

**Done when:** Only one entry point exists; the README matches.

---

## §4 Tests for the fetcher (lightweight)

**Why:** Right now `paperTrading.ts` and `riskEngine.ts` have tests but `fetcher.py` doesn't — and that's the highest-blast-radius module (if Twelve Data changes its response shape, signals die silently).

**Steps**
1. Add `services/data/tests/test_fetcher.py` with:
   - A fixture JSON file capturing one real Twelve Data response.
   - A test using `respx` (or `pytest-httpx`) to mock the API and assert `fetcher.fetch_candles()` returns the expected normalized shape.
   - A negative test: API returns an error envelope → fetcher raises / logs but doesn't crash the loop.
2. Wire to pytest via the existing `pyproject.toml` test extras.
3. Add `npm run test:data` script that just shells to `cd services/data && pytest -q` (optional convenience).

**Done when:** `pytest services/data/tests` exits 0 with at least 2 tests covering happy + error paths.

---

## What this plan deliberately leaves out

- **News ingestion** — that belongs to Phase 3 (it's an AI-enabling concern, not raw market data). See `03-phase-3-ai-intelligence.md` §2.
- **Hypertable for `Indicator` / `Signal` / `Trade`** — premature.
- **Migration to a separate read replica** — way too early.

## Estimated effort
- §1 hypertable: **30 min**
- §2 RiskLog persistence: **1–2 hr** (including tests)
- §3 worker.py cleanup: **15 min**
- §4 fetcher tests: **1 hr**

**Total: half a day.**
