# Prisma schema archive (historical)

This is the schema and full SQL migration history as Prisma left them, preserved
when the Express runtime was removed (plan 11, Phase 8). **Alembic is now the
authoritative migration tool** — `services/backend/migrations`.

## Why it is kept

The Alembic baseline revision *adopts* this schema rather than recreating it: on
an existing database it detects a live `Candle` table and no-ops, so it never
replays these files. That means this directory is the only record of **how the
production schema came to be** — twelve migrations from the initial layout to the
raw-signal feed.

It is documentation, not something to run. Nothing in the codebase reads it.

## Reading it

The SQL is plain Postgres DDL and can be read directly. `schema.prisma` is the
declarative model that generated it, and remains the clearest single description
of the original data model — including the comments explaining each table's
purpose, which were carried into
`services/backend/app/db/models.py`.

## Relationship to the live database

* Table and column names are unchanged: PascalCase tables, camelCase columns,
  `TIMESTAMP(3)` without time zone holding naive UTC.
* The SQLAlchemy models mirror this exactly — `alembic revision --autogenerate`
  produces an empty diff, and `services/backend/tests/test_schema_drift.py`
  fails the build if it ever stops doing so.
* The `_prisma_migrations` table is deliberately left in the database as a record
  of which of these were applied. Alembic ignores it
  (`migrations/env.py: IGNORED_TABLES`).

## Recovering the Express implementation

The runtime that used this schema is in the archive tag:

```bash
git checkout archive/express-pre-plan11 -- apps/api    # whole API
git show archive/express-pre-plan11:apps/api/src/risk/riskEngine.ts
```
