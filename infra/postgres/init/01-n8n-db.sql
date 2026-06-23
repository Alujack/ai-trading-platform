-- Give n8n its own database for workflow/internal state, kept separate from the
-- trading schema. n8n's Postgres *node* (the one that writes NewsEvent) still
-- points at the `trading` database; this DB is only for n8n's own bookkeeping.
--
-- Runs once, on first init of an empty Postgres data volume
-- (docker-entrypoint-initdb.d). If the volume already exists, create it manually:
--   docker compose exec postgres psql -U postgres -c "CREATE DATABASE n8n;"
SELECT 'CREATE DATABASE n8n'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'n8n')\gexec
