"""Shared helpers for the integration tests."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jobscheduler.algorithms import make_scheduler      # noqa: E402
from jobscheduler.db import Database                    # noqa: E402
from jobscheduler.engine import Engine                  # noqa: E402

SCRIPTS = os.path.join(ROOT, "examples", "scripts")
PY = shlex.quote(sys.executable)


class SchedulerTestCase(unittest.TestCase):
    """Creates a throw-away data directory and offers add_job()/run_engine() shortcuts."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="jobsched_test_")
        self.db = Database(os.path.join(self.home, "jobs.db"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def add_job(self, name, command, priority=5, next_run=None, depends_on=(), **extra):
        fields = dict(name=name, command=command, priority=priority,
                      schedule_type="once", next_run=time.time() if next_run is None else next_run)
        fields.update(extra)
        job_id = self.db.add_job(**fields)
        if depends_on:
            self.db.set_dependencies(job_id, [self.db.get_job_by_name(d)["id"] for d in depends_on])
        return job_id

    def make_engine(self, algorithm="fcfs", workers=1, quantum=2.0, aging=0.0, **kw):
        return Engine(self.db, self.home, make_scheduler(algorithm, quantum, aging),
                      workers=workers, tick=0.03, kill_grace=1.0, echo=False, **kw)

    def run_engine(self, **kw):
        until = kw.pop("until_idle", True)
        engine = self.make_engine(**kw)
        engine.run(until_idle=until, duration=60)
        return engine

    def job(self, name):
        return self.db.get_job_by_name(name)

    def runs(self, name):
        return sorted(self.db.get_runs(self.job(name)["id"], limit=100), key=lambda r: r["id"])

    def start_order(self):
        runs = sorted(self.db.get_runs(limit=1000), key=lambda r: r["start_time"])
        return [r["job_name"] for r in runs]

    @staticmethod
    def pump(engine, condition, timeout=15.0):
        """Call engine.step() until condition() is true (or fail after `timeout` seconds)."""
        end = time.time() + timeout
        while time.time() < end:
            engine.step()
            if condition():
                return True
            time.sleep(0.03)
        return False

    @staticmethod
    def process_state(pid):
        """First letter of `ps` STAT (R/S = running or sleeping, T = stopped).  Works on Linux and macOS."""
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
        return out.strip()[:1]
