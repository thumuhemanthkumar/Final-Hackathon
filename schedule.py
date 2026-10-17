"""Helpers that compute when recurring jobs fire next."""

from .cron import CronSchedule

RECURRING = ("periodic", "cron")


def next_occurrence(schedule_type, spec, after, anchor=None):
    """Return the next firing time strictly after `after` for a recurring schedule.

    periodic: fixed-rate schedule anchored at `anchor` (missed slots are skipped).
    cron:     next time matching the cron expression.
    """
    if schedule_type == "periodic":
        interval = int(spec)
        if anchor is None:
            return after + interval
        if anchor > after:
            return anchor
        return anchor + (int((after - anchor) // interval) + 1) * interval
    if schedule_type == "cron":
        return CronSchedule(spec).next_after(after)
    return None
