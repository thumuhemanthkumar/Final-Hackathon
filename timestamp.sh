#!/bin/sh
# Sample automated task: append the current date/time to a log file.
# Usage: timestamp.sh [FILE]     (default: $JOBSCHED_HOME/timestamps.log)
FILE=${1:-"${JOBSCHED_HOME:-$HOME/.jobsched}/timestamps.log"}
mkdir -p "$(dirname "$FILE")"
echo "$(date '+%Y-%m-%d %H:%M:%S') - timestamp written by Job Scheduler" >> "$FILE"
echo "Wrote timestamp to $FILE"
tail -n 1 "$FILE"
