#!/bin/sh
# Sample automated task: create a timestamped .tar.gz backup and keep only the newest 5.
# Usage: backup.sh [SOURCE_DIR] [DEST_DIR]     (works on Linux and macOS)
set -eu
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SRC=${1:-"$SCRIPT_DIR/../sample_data"}
DEST=${2:-"${JOBSCHED_HOME:-$HOME/.jobsched}/backups"}

[ -d "$SRC" ] || { echo "backup: source directory not found: $SRC" >&2; exit 1; }
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE="$DEST/backup_$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$(dirname "$SRC")" "$(basename "$SRC")"
echo "Backup created: $ARCHIVE"
ls -l "$ARCHIVE"
# retention: delete everything but the 5 newest archives
ls -1t "$DEST"/backup_*.tar.gz 2>/dev/null | tail -n +6 | while read -r old; do
    rm -f "$old"
    echo "Removed old backup: $old"
done
