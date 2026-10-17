"""CPU-scheduling policies used to pick the next job from the ready queue.

Every policy answers one question: *"Of all jobs that are ready, which one gets the
next free worker slot?"*  A policy is a sort key; the lowest key wins.

FCFS      - First-Come First-Served.  Non-preemptive.  Ordered by arrival time.
PRIORITY  - Lowest priority number (1 = highest) first.  Non-preemptive; optional
            *aging* raises the priority of waiting jobs so nothing starves.
RR        - Round Robin.  FIFO ready queue + a fixed time quantum.  A job that
            uses its whole quantum while others wait is stopped (SIGSTOP), moved to
            the back of the queue, and later continued (SIGCONT).
"""

from .validation import ValidationError


class Candidate(object):
    """A ready-to-run entry: either a new job or a suspended process waiting to continue."""
    __slots__ = ("job", "arrival", "proc")

    def __init__(self, job, arrival, proc=None):
        self.job = job            # job row (dict)
        self.arrival = arrival    # time it entered the ready queue
        self.proc = proc          # ManagedProcess if it was already started and suspended


class BaseScheduler(object):
    name = "base"
    time_slice = None             # seconds, or None for non-preemptive policies

    def sort_key(self, candidate, now):
        raise NotImplementedError

    def select(self, candidates, now):
        """Return the candidate that should run next (candidates must be non-empty)."""
        return min(candidates, key=lambda c: self.sort_key(c, now))

    def describe(self):
        return self.name


class FCFS(BaseScheduler):
    name = "fcfs"

    def sort_key(self, candidate, now):
        return (candidate.arrival, candidate.job["id"])

    def describe(self):
        return "FCFS (first-come first-served, non-preemptive)"


class PriorityScheduler(BaseScheduler):
    name = "priority"

    def __init__(self, aging=0.0):
        self.aging = float(aging)         # seconds of waiting that earn +1 priority level (0 = off)

    def effective_priority(self, candidate, now):
        priority = float(candidate.job["priority"])
        if self.aging > 0:
            priority -= max(0.0, now - candidate.arrival) / self.aging
        return priority

    def sort_key(self, candidate, now):
        return (self.effective_priority(candidate, now), candidate.arrival, candidate.job["id"])

    def describe(self):
        extra = ", aging every %gs" % self.aging if self.aging > 0 else ""
        return "PRIORITY (1 = highest, non-preemptive%s)" % extra


class RoundRobin(BaseScheduler):
    name = "rr"

    def __init__(self, quantum=2.0):
        self.quantum = float(quantum)
        self.time_slice = self.quantum

    def sort_key(self, candidate, now):
        return (candidate.arrival, candidate.job["id"])      # FIFO ready queue

    def describe(self):
        return "ROUND ROBIN (time quantum %gs, preemptive)" % self.quantum


ALGORITHMS = ("fcfs", "priority", "rr")


def make_scheduler(name, quantum=2.0, aging=0.0):
    name = str(name).lower()
    if name == "fcfs":
        return FCFS()
    if name == "priority":
        if aging < 0:
            raise ValidationError("aging must be >= 0")
        return PriorityScheduler(aging)
    if name in ("rr", "roundrobin", "round-robin"):
        if quantum <= 0:
            raise ValidationError("time quantum must be > 0")
        return RoundRobin(quantum)
    raise ValidationError("unknown algorithm %r (choose from: %s)" % (name, ", ".join(ALGORITHMS)))
