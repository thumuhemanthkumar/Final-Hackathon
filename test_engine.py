"""Integration tests: real child processes are forked, supervised, signalled and reaped."""

import os
import time
import unittest

from tests.helpers import PY, SCRIPTS, SchedulerTestCase


class SchedulingOrderTests(SchedulerTestCase):
    def test_fcfs_runs_in_arrival_order(self):
        now = time.time()
        self.add_job("third", "echo 3", priority=1, next_run=now - 1)
        self.add_job("first", "echo 1", priority=9, next_run=now - 3)
        self.add_job("second", "echo 2", priority=5, next_run=now - 2)
        self.run_engine(algorithm="fcfs")
        self.assertEqual(self.start_order(), ["first", "second", "third"])

    def test_priority_runs_highest_priority_first(self):
        now = time.time() - 1
        self.add_job("low", "echo low", priority=9, next_run=now)
        self.add_job("high", "echo high", priority=1, next_run=now)
        self.add_job("mid", "echo mid", priority=5, next_run=now)
        self.run_engine(algorithm="priority")
        self.assertEqual(self.start_order(), ["high", "mid", "low"])

    def test_round_robin_preempts_and_all_jobs_finish(self):
        burn = "%s %s/cpu_burn.py 1.2 %%s" % (PY, SCRIPTS)
        self.add_job("A", burn % "A")
        self.add_job("B", burn % "B")
        self.run_engine(algorithm="rr", quantum=0.4)
        a, b = self.runs("A")[0], self.runs("B")[0]
        self.assertEqual((a["status"], b["status"]), ("success", "success"))
        self.assertGreaterEqual(a["preemptions"], 1)
        self.assertGreaterEqual(b["preemptions"], 1)
        # both jobs were in the system at the same time -> their wall-clock intervals overlap
        self.assertLess(b["start_time"], a["end_time"])
        # wall time exceeds active time because of the time spent suspended
        self.assertGreater(a["duration"], a["active_time"])
        self.assertIn("A: finished", a["output"])

    def test_parallel_workers(self):
        self.add_job("p1", "sleep 1")
        self.add_job("p2", "sleep 1")
        started = time.time()
        self.run_engine(workers=2)
        self.assertLess(time.time() - started, 1.9)      # two 1 s jobs overlapped


class DependencyTests(SchedulerTestCase):
    def test_dependent_waits_for_prerequisite(self):
        now = time.time()
        self.add_job("prereq", "sleep 1; echo built", next_run=now + 0.5)
        self.add_job("child", "echo deploy", next_run=now - 5, depends_on=["prereq"], priority=1)
        self.run_engine()
        self.assertEqual(self.start_order(), ["prereq", "child"])
        self.assertGreaterEqual(self.runs("child")[0]["start_time"], self.runs("prereq")[0]["end_time"])

    def test_failed_prerequisite_blocks_chain(self):
        self.add_job("a", "exit 1")
        self.add_job("b", "echo b", depends_on=["a"])
        self.add_job("c", "echo c", depends_on=["b"])
        self.run_engine()
        self.assertEqual(self.job("a")["state"], "failed")
        self.assertEqual(self.job("b")["state"], "failed")
        self.assertEqual(self.job("c")["state"], "failed")
        self.assertEqual(self.runs("b")[0]["status"], "skipped")
        self.assertEqual(self.runs("c")[0]["status"], "skipped")
        self.assertIn("'a' failed", self.runs("b")[0]["message"])
        self.assertTrue(os.path.exists(os.path.join(self.home, "failures.log")))

    def test_cycle_detection(self):
        a = self.add_job("a", "true")
        b = self.add_job("b", "true", depends_on=["a"])
        self.assertTrue(self.db.creates_cycle(a, [b]))
        self.assertFalse(self.db.creates_cycle(b, [a]))


class FailureHandlingTests(SchedulerTestCase):
    def test_retry_until_success(self):
        counter = os.path.join(self.home, "count")
        self.add_job("flaky", "sh %s/flaky.sh 3 %s" % (SCRIPTS, counter), max_retries=3, retry_delay=0)
        self.run_engine()
        runs = self.runs("flaky")
        self.assertEqual([r["status"] for r in runs], ["failed", "failed", "success"])
        self.assertEqual([r["attempt"] for r in runs], [1, 2, 3])
        self.assertEqual(self.job("flaky")["state"], "completed")

    def test_retries_exhausted_marks_failed(self):
        self.add_job("bad", "echo oops; exit 3", max_retries=1, retry_delay=0)
        self.run_engine()
        runs = self.runs("bad")
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(r["status"] == "failed" and r["exit_code"] == 3 for r in runs))
        self.assertIn("oops", runs[0]["output"])
        self.assertEqual(self.job("bad")["state"], "failed")
        with open(os.path.join(self.home, "failures.log")) as handle:
            self.assertIn("job=bad", handle.read())

    def test_timeout_kills_job(self):
        self.add_job("slow", "sleep 30", timeout=1)
        started = time.time()
        self.run_engine()
        self.assertLess(time.time() - started, 8)
        run = self.runs("slow")[0]
        self.assertEqual(run["status"], "timeout")
        self.assertEqual(run["exit_code"], -15)            # SIGTERM
        self.assertEqual(self.job("slow")["state"], "failed")

    def test_timeout_escalates_to_sigkill(self):
        self.add_job("stubborn", "trap '' TERM; while true; do sleep 1; done", timeout=1)
        self.run_engine()
        run = self.runs("stubborn")[0]
        self.assertEqual(run["status"], "timeout")
        self.assertEqual(run["exit_code"], -9)             # SIGKILL

    def test_missing_command_reports_127(self):
        self.add_job("ghost", "definitely_not_a_command_xyz")
        self.run_engine()
        self.assertEqual(self.runs("ghost")[0]["exit_code"], 127)
        self.assertEqual(self.job("ghost")["state"], "failed")

    def test_logging_fields(self):
        self.add_job("logme", "echo line1; echo err >&2; exit 0")
        self.run_engine()
        run = self.runs("logme")[0]
        self.assertGreater(run["start_time"], 0)
        self.assertGreaterEqual(run["end_time"], run["start_time"])
        self.assertAlmostEqual(run["duration"], run["end_time"] - run["start_time"], places=3)
        self.assertEqual(run["exit_code"], 0)
        self.assertIn("line1", run["output"])
        self.assertIn("err", run["output"])               # stderr is captured too


class RecurringAndControlTests(SchedulerTestCase):
    def test_periodic_job_runs_repeatedly(self):
        now = time.time()
        self.add_job("tick", "echo tick", schedule_type="periodic", schedule_spec="1",
                     next_run=now, sched_next=now)
        engine = self.make_engine()
        engine.run(duration=3.4)
        self.assertGreaterEqual(len(self.runs("tick")), 3)
        self.assertEqual(self.job("tick")["state"], "pending")        # waiting for its next slot

    def test_future_one_time_job_waits(self):
        self.add_job("later", "echo later", next_run=time.time() + 1.2)
        started = time.time()
        self.run_engine()
        self.assertGreaterEqual(self.runs("later")[0]["start_time"] - started, 1.0)

    def test_pending_paused_job_is_not_run(self):
        self.add_job("held", "echo held", state="paused")
        self.add_job("free", "echo free")
        self.run_engine()
        self.assertEqual(self.start_order(), ["free"])
        self.assertEqual(self.job("held")["state"], "paused")

    def test_pause_and_resume_running_job_with_signals(self):
        jid = self.add_job("worker", "for i in 1 2 3 4 5 6; do echo step $i; sleep 1; done")
        engine = self.make_engine()
        self.assertTrue(self.pump(engine, lambda: self.job("worker")["state"] == "running"))
        pid = self.db.get_runs(jid, limit=1)[0]["pid"]
        self.db.update_job(jid, state="paused")                        # what `pause` does
        self.assertTrue(self.pump(engine, lambda: self.process_state(pid) == "T"))   # SIGSTOP applied
        self.db.update_job(jid, state="running")                       # what `resume` does
        self.assertTrue(self.pump(engine, lambda: self.process_state(pid) != "T"))   # SIGCONT applied
        self.assertTrue(self.pump(engine, lambda: self.job("worker")["state"] == "completed", timeout=20))
        self.assertEqual(self.runs("worker")[0]["status"], "success")

    def test_kill_running_job(self):
        jid = self.add_job("victim", "sleep 60")
        engine = self.make_engine()
        self.assertTrue(self.pump(engine, lambda: self.job("victim")["state"] == "running"))
        self.db.update_job(jid, kill_requested=1)
        self.assertTrue(self.pump(engine, lambda: self.job("victim")["state"] == "failed"))
        self.assertEqual(self.runs("victim")[0]["status"], "killed")

    def test_second_engine_is_refused(self):
        from jobscheduler.engine import EngineError
        first = self.make_engine()
        first._acquire_lock()
        try:
            with self.assertRaises(EngineError):
                self.make_engine()._acquire_lock()
        finally:
            first._release_lock()

    def test_recovery_of_orphaned_running_job(self):
        jid = self.add_job("orphan", "echo recovered", state="running", next_run=None)
        self.db.add_run(job_id=jid, job_name="orphan", start_time=time.time(), status="running")
        self.run_engine()
        statuses = [r["status"] for r in self.runs("orphan")]
        self.assertEqual(statuses, ["interrupted", "success"])


if __name__ == "__main__":
    unittest.main()
