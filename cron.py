"""A small cron-expression parser (5 fields: minute hour day-of-month month day-of-week).

Supported syntax per field: '*', numbers, lists (1,5,9), ranges (1-5),
steps (*/10, 0-30/5) and month/day names (jan, mon-fri).  Also supports the
shortcuts @hourly, @daily, @midnight, @weekly, @monthly and @yearly.
Day-of-month and day-of-week follow standard cron semantics: if both are
restricted, a day matches when EITHER matches.
"""

from datetime import date, datetime, timedelta

from .validation import ValidationError


class CronError(ValidationError):
    """Raised for malformed cron expressions."""


ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
MONTH_NAMES = {n: i for i, n in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DAY_NAMES = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
FIELDS = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day-of-month", 1, 31, None),
    ("month", 1, 12, MONTH_NAMES),
    ("day-of-week", 0, 7, DAY_NAMES),
)


def _convert(token, label, names):
    token = token.lower()
    if names and token in names:
        return names[token]
    if token.isdigit():
        return int(token)
    raise CronError("cron %s: invalid value %r" % (label, token))


def _parse_field(text, label, lo, hi, names):
    values = set()
    if not text:
        raise CronError("cron %s: empty field" % label)
    for part in text.split(","):
        if not part:
            raise CronError("cron %s: empty list element in %r" % (label, text))
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError("cron %s: invalid step in %r" % (label, part))
            step = int(step_text)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _convert(a, label, names), _convert(b, label, names)
        else:
            start = _convert(base, label, names)
            end = hi if "/" in part else start
        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise CronError("cron %s: %r is outside the allowed range %d-%d" % (label, part, lo, hi))
        values.update(range(start, end + 1, step))
    return values


class CronSchedule(object):
    """A parsed cron expression that can compute its next firing time."""

    def __init__(self, expression):
        expression = str(expression).strip()
        expression = ALIASES.get(expression.lower(), expression)
        parts = expression.split()
        if len(parts) != 5:
            raise CronError("cron expression needs 5 fields (min hour dom month dow), got %d: %r"
                            % (len(parts), expression))
        self.expr = " ".join(parts)
        parsed = [_parse_field(p, f[0], f[1], f[2], f[3]) for p, f in zip(parts, FIELDS)]
        self.minutes = sorted(parsed[0])
        self.hours = sorted(parsed[1])
        self.doms = parsed[2]
        self.months = parsed[3]
        self.dows = {d % 7 for d in parsed[4]}          # 7 == Sunday == 0
        self.dom_star = parts[2].startswith("*")
        self.dow_star = parts[4].startswith("*")

    def _day_matches(self, day):
        dom_ok = day.day in self.doms
        dow_ok = ((day.weekday() + 1) % 7) in self.dows   # Python: Mon=0; cron: Sun=0
        if self.dom_star and self.dow_star:
            return True
        if self.dom_star:
            return dow_ok
        if self.dow_star:
            return dom_ok
        return dom_ok or dow_ok

    def next_after(self, timestamp):
        """Return the first matching epoch time strictly after `timestamp` (local time)."""
        start = datetime.fromtimestamp(timestamp).replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.date()
        for _ in range(366 * 8):          # 8 years covers leap-day schedules
            if day.month in self.months and self._day_matches(day):
                for hour in self.hours:
                    if day == start.date() and hour < start.hour:
                        continue
                    for minute in self.minutes:
                        if day == start.date() and hour == start.hour and minute < start.minute:
                            continue
                        return datetime(day.year, day.month, day.day, hour, minute).timestamp()
            day += timedelta(days=1)
        raise CronError("cron expression %r never matches a real date" % self.expr)
