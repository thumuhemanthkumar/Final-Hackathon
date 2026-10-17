#!/bin/sh
# Test helper: fails until it has been started N times, then succeeds (demonstrates retries).
# Usage: flaky.sh SUCCEED_ON_ATTEMPT COUNTER_FILE
WANT=${1:-3}
COUNTER=${2:-"${TMPDIR:-/tmp}/jobsched_flaky.count"}
N=0
[ -f "$COUNTER" ] && N=$(cat "$COUNTER")
N=$((N + 1))
echo "$N" > "$COUNTER"
echo "flaky.sh: attempt $N (succeeds on attempt $WANT)"
if [ "$N" -ge "$WANT" ]; then
    echo "flaky.sh: success"
    exit 0
fi
echo "flaky.sh: simulated failure" >&2
exit 1
