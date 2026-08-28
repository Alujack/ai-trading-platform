#!/usr/bin/env sh
# Create (or recreate) the database the DB-backed tests use, and migrate it.
#
# The integration tests refuse to run unless TEST_DATABASE_URL names a database
# whose name ends in `_test`, so they can never truncate a real trading database.
#
#   sh services/backend/scripts/setup_test_db.sh
#   export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:${POSTGRES_PORT:-5432}/trading_test
#   cd services/backend && .venv/bin/python -m pytest tests/ -q
set -eu

CONTAINER="${POSTGRES_CONTAINER:-trading-postgres}"
USER_NAME="${POSTGRES_USER:-postgres}"
PASSWORD="${POSTGRES_PASSWORD:-postgres}"
PORT="${POSTGRES_PORT:-5432}"
DB="${TEST_DB_NAME:-trading_test}"

case "$DB" in
  *_test) ;;
  *) echo "refusing: TEST_DB_NAME must end in _test (got '$DB')" >&2; exit 1 ;;
esac

echo "recreating $DB in container $CONTAINER"
docker exec "$CONTAINER" psql -U "$USER_NAME" -c "DROP DATABASE IF EXISTS $DB;"
docker exec "$CONTAINER" psql -U "$USER_NAME" -c "CREATE DATABASE $DB;"

URL="postgresql://$USER_NAME:$PASSWORD@localhost:$PORT/$DB"
echo "migrating $DB"
cd "$(dirname "$0")/.."
DATABASE_URL="$URL" .venv/bin/alembic upgrade head

echo
echo "ready. Run the DB-backed tests with:"
echo "  export TEST_DATABASE_URL=$URL"
echo "  cd services/backend && .venv/bin/python -m pytest tests/ -q"
