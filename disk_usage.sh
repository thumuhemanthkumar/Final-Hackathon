#!/bin/sh
# Sample automated task: report disk usage of / and fail (exit 1) when above a threshold.
# Usage: disk_usage.sh [THRESHOLD_PERCENT]     (default 90)
THRESHOLD=${1:-90}
USAGE=$(df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}')
echo "Disk usage of / is ${USAGE}% (threshold ${THRESHOLD}%)"
df -h /
if [ "$USAGE" -ge "$THRESHOLD" ]; then
    echo "WARNING: disk usage is above the threshold!" >&2
    exit 1
fi
