import unittest
from datetime import datetime

from tests import helpers  # noqa: F401  (sets sys.path)
from jobscheduler.cron import CronError, CronSchedule
from jobscheduler.validation import (ValidationError, parse_datetime, parse_duration,
                                     validate_command, validate_int, validate_name)

SAT_1007 = datetime(2026, 10, 17, 10, 7).timestamp()     # Saturday 2026-10-17 10:07


def nxt(expr, ts=SAT_1007):
    return datetime.fromtimestamp(CronSchedule(expr).next_after(ts))


class CronTests(unittest.TestCase):
    def test_every_15_minutes(self):
        self.assertEqual(nxt("*/15 * * * *"), datetime(2026, 10, 17, 10, 15))

    def test_daily_time_rolls_to_tomorrow(self):
        self.assertEqual(nxt("30 2 * * *"), datetime(2026, 10, 18, 2, 30))

    def test_weekdays_skip_weekend(self):
        self.assertEqual(nxt("0 9 * * mon-fri"), datetime(2026, 10, 19, 9, 0))

    def test_lists_and_ranges(self):
        self.assertEqual(nxt("5,20,40 11-12 * * *"), datetime(2026, 10, 17, 11, 5))

    def test_alias(self):
        self.assertEqual(nxt("@hourly"), datetime(2026, 10, 17, 11, 0))

    def test_strictly_after(self):
        exact = datetime(2026, 10, 17, 10, 15).timestamp()
        self.assertEqual(nxt("*/15 * * * *", exact), datetime(2026, 10, 17, 10, 30))

    def test_dom_or_dow(self):
        # 1st of month OR Monday: next after Sat 17th is Mon 19th
        self.assertEqual(nxt("0 0 1 * mon"), datetime(2026, 10, 19, 0, 0))

    def test_invalid(self):
        for bad in ("* * * *", "61 * * * *", "* 24 * * *", "a b c d e", "*/0 * * * *", "5-1 * * * *"):
            with self.assertRaises(CronError):
                CronSchedule(bad)


class ValidationTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(parse_duration("90"), 90)
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1d"), 86400)

    def test_bad_durations(self):
        for bad in ("abc", "-5", "1.5h", ""):
            with self.assertRaises(ValidationError):
                parse_duration(bad)
        with self.assertRaises(ValidationError):
            parse_duration("0", minimum=1)

    def test_names(self):
        self.assertEqual(validate_name("backup-1.nightly"), "backup-1.nightly")
        for bad in ("", "has space", "-lead", "a" * 65, "semi;colon"):
            with self.assertRaises(ValidationError):
                validate_name(bad)

    def test_command_and_int(self):
        self.assertEqual(validate_command("  echo hi "), "echo hi")
        with self.assertRaises(ValidationError):
            validate_command("   ")
        self.assertEqual(validate_int("3", "x", 1, 10), 3)
        with self.assertRaises(ValidationError):
            validate_int("11", "x", 1, 10)
        with self.assertRaises(ValidationError):
            validate_int("abc", "x", 1, 10)

    def test_datetime(self):
        self.assertEqual(datetime.fromtimestamp(parse_datetime("2026-12-25 08:30")),
                         datetime(2026, 12, 25, 8, 30))
        with self.assertRaises(ValidationError):
            parse_datetime("tomorrow-ish")


if __name__ == "__main__":
    unittest.main()
