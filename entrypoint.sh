#!/usr/bin/env sh
# Container entrypoint: run DB migrations, then exec the app server.
# Per #030: "First deploy runs `alembic upgrade head` on startup."
#
# Fail loud: if migrations fail, the container exits non-zero and Coolify
# surfaces the failure — never silently start with a stale schema.

set -e

echo "Running alembic upgrade head..."
alembic upgrade head

echo "Starting app: $@"
exec "$@"
