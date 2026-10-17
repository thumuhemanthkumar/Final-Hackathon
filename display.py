"""Formatting helpers: tables, timestamps, durations and (optional) colours."""

import os
import sys
import time

COLORS = {"running": "36", "pending": "33", "paused": "35", "completed": "32", "failed": "31",
          "success": "32", "timeout": "31", "killed": "31", "skipped": "31", "interrupted": "35",
          "retrying": "33"}


def use_color():
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def paint(text, key):
    if use_color() and key in COLORS:
        return "\033[%sm%s\033[0m" % (COLORS[key], text)
    return text


def fmt_time(ts):
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_duration(seconds):
    if seconds is None:
        return "-"
    if seconds < 60:
        return "%.2fs" % seconds
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return "%dm%02ds" % (minutes, secs)
    hours, minutes = divmod(minutes, 60)
    return "%dh%02dm" % (hours, minutes)


def fmt_interval(seconds):
    seconds = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0 and seconds >= size:
            return "%d%s" % (seconds // size, unit)
    return "%ds" % seconds


def fmt_next(ts, now=None):
    """'2026-10-17 10:00:00 (in 5m00s)' style text for upcoming times."""
    if ts is None:
        return "-"
    now = time.time() if now is None else now
    delta = ts - now
    when = fmt_time(ts)
    if delta > 0:
        return "%s (in %s)" % (when, fmt_duration(delta))
    return "%s (due)" % when


def describe_schedule(job):
    kind = job["schedule_type"]
    if kind == "periodic":
        return "every %s" % fmt_interval(job["schedule_spec"])
    if kind == "cron":
        return "cron '%s'" % job["schedule_spec"]
    if kind == "once":
        return "once"
    return "manual"


def table(headers, rows, state_col=None):
    """Render a simple left-aligned text table.  `state_col` colours that column by its value."""
    text_rows = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in text_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def line(cells, colour=False):
        out = []
        for i, cell in enumerate(cells):
            padded = cell.ljust(widths[i])
            if colour and i == state_col:
                padded = paint(padded, cell.strip().split()[0].lower())
            out.append(padded)
        return "  ".join(out).rstrip()
    lines = [line(headers), "  ".join("-" * w for w in widths)]
    lines.extend(line(r, colour=True) for r in text_rows)
    return "\n".join(lines)


def clip(text, width):
    text = str(text).replace("\n", " ")
    return text if len(text) <= width else text[:width - 3] + "..."
