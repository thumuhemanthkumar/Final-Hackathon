import unittest

from tests import helpers  # noqa: F401
from jobscheduler.algorithms import (Candidate, FCFS, PriorityScheduler, RoundRobin,
                                     make_scheduler)
from jobscheduler.validation import ValidationError


def cand(job_id, priority, arrival):
    return Candidate({"id": job_id, "priority": priority}, arrival)


class AlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.ready = [cand(1, 5, 10.0), cand(2, 1, 12.0), cand(3, 3, 11.0)]

    def test_fcfs_picks_earliest_arrival(self):
        self.assertEqual(FCFS().select(self.ready, 20).job["id"], 1)

    def test_priority_picks_lowest_number(self):
        self.assertEqual(PriorityScheduler().select(self.ready, 20).job["id"], 2)

    def test_priority_tie_broken_by_arrival(self):
        tie = [cand(1, 2, 15.0), cand(2, 2, 12.0)]
        self.assertEqual(PriorityScheduler().select(tie, 20).job["id"], 2)

    def test_aging_prevents_starvation(self):
        old_low = cand(1, 9, 0.0)          # waited 100s at priority 9
        new_high = cand(2, 2, 99.0)        # just arrived at priority 2
        self.assertEqual(PriorityScheduler().select([old_low, new_high], 100).job["id"], 2)
        # with aging of 10s per level the old job has effective priority -1 and wins
        self.assertEqual(PriorityScheduler(aging=10).select([old_low, new_high], 100).job["id"], 1)

    def test_round_robin_is_fifo_with_quantum(self):
        rr = RoundRobin(0.5)
        self.assertEqual(rr.select(self.ready, 20).job["id"], 1)
        self.assertEqual(rr.time_slice, 0.5)
        self.assertIsNone(FCFS().time_slice)

    def test_factory(self):
        self.assertIsInstance(make_scheduler("rr", 1.0), RoundRobin)
        with self.assertRaises(ValidationError):
            make_scheduler("lottery")
        with self.assertRaises(ValidationError):
            make_scheduler("rr", 0)


if __name__ == "__main__":
    unittest.main()
