#!/usr/bin/env sh
# V2 owns a fresh Alembic history and never mutates the rollback database.
set -e

if [ "$1" != "python" ] || [ "$2" != "-m" ] || [ "$3" != "leetcode_coach_v2.scheduler" ]; then
    echo "Running V2 migrations..."
    alembic -c alembic-v2.ini upgrade head
fi

echo "Starting V2: $@"
exec "$@"
