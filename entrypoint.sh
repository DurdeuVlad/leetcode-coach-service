#!/usr/bin/env sh
# Container entrypoint: the webhook app owns DB migrations; scheduler does not.
# Per #030: "First deploy runs `alembic upgrade head` on startup."
#
# Fail loud: if migrations fail, the container exits non-zero and Coolify
# surfaces the failure — never silently start with a stale schema.

set -e

if [ "$1" != "python" ] || [ "$2" != "-m" ] || [ "$3" != "leetcode_coach.scheduler" ]; then
    echo "Running alembic upgrade head..."
    alembic upgrade head
fi

echo "Starting app: $@"
exec "$@"
