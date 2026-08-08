#!/usr/bin/env sh
# Container entrypoint: runs alembic migrations, then starts the app.
# The scheduler-only command skips migrations (the webhook app owns them).
set -e

if [ "$1" != "python" ] || [ "$2" != "-m" ] || [ "$3" != "leetcode_coach.scheduler" ]; then
    echo "Running alembic upgrade head..."
    alembic upgrade head
fi

echo "Starting app: $@"
exec "$@"
