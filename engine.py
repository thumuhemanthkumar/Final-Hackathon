"""The scheduling engine: dispatches jobs, supervises child processes, applies failure policy.

One engine process runs a loop ("tick") every fraction of a second:

    1. apply_control      - react to pause / resume / kill / delete issued from the CLI
    2. service_processes  - drain output, reap finished children, enforce timeouts
    3. enforce_time_slice - Round Robin preemption (SIGSTOP)
    4. block_failed_deps  - fail jobs whose prerequisite failed permanently
    5. dispatch           - fill free worker slots using the chosen scheduling algorithm

The CLI and the engine communicate only through the SQLite database, so jobs can be
added, paused, edited ... while the engine is running.
"""

import fcntl
import os
import signal
import time

from .algorithms import Candidate
from .process import ManagedProcess, RUNNING, SUSPENDED, PAUSED, describe_exit
from .schedule import RECURRING, next_occurrence


class EngineError(Exception):
    """Raised for engine-level problems (e.g. a second scheduler instance)."""


class Engine(object):
    def __init__(self, db, home, scheduler, workers=1, tick=0.25, kill_grace=2.0, echo=True):
        self.db = db
        self.home = home
        self.scheduler = scheduler
        self.workers = workers
        self.tick = tick
        self.kill_grace = kill_grace      # seconds between SIGTERM and SIGKILL
        self.echo = echo
        self.procs = {}                   # job_id -> ManagedProcess
        self.stop_requested = False
        self._lock_file = None
        self.log_path = os.path.join(home, "scheduler.log")
        self.failure_log_path = os.path.join(home, "failures.log")

    # ------------------------------------------------------------------ logging
    def log(self, message):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        if self.echo:
            print(line, flush=True)
        try:
            with open(self.log_path, "a") as handle:
                handle.write("%s %s\n" % (time.strftime("%Y-%m-%d"), line))
        except OSError:
            pass

    # --------------------------------------------------------------- main loop
    def run(self, until_idle=False, duration=None):
        """Run until stopped, until nothing is left to do, or until `duration` seconds passed."""
        self._acquire_lock()
        previous = self._install_signal_handlers()
        try:
            self._recover()
            self.log("scheduler started: %s | workers=%d" % (self.scheduler.describe(), self.workers))
            deadline = time.time() + duration if duration else None
            idle_ticks = 0
            while not self.stop_requested:
                self.step()
                idle_ticks = idle_ticks + 1 if (until_idle and self.is_idle()) else 0
                if idle_ticks >= 2:
                    self.log("no more work: scheduler is idle, exiting")
                    break
                if deadline is not None and time.time() >= deadline:
                    self.log("time limit reached, shutting down")
                    break
                time.sleep(self.tick)
            if self.stop_requested:
                self.log("stop requested, shutting down")
        finally:
            self._shutdown()
            self._restore_signal_handlers(previous)
            self._release_lock()

    def step(self):
        now = time.time()
        self._apply_control(now)
        self._service_processes(now)
        self._enforce_time_slice(now)
        self._block_failed_dependents(now)
        self._dispatch(time.time())

    # ------------------------------------------------------- lock / signals / recovery
    def _acquire_lock(self):
        path = os.path.join(self.home, "engine.lock")
        self._lock_file = open(path, "a+")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            self._lock_file = None
            raise EngineError("another scheduler instance is already running (lock: %s)" % path)

    def _release_lock(self):
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None

    def _install_signal_handlers(self):
        def handler(signum, frame):
            self.stop_requested = True
        saved = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                saved[sig] = signal.signal(sig, handler)
            except ValueError:            # not in the main thread
                pass
        return saved

    @staticmethod
    def _restore_signal_handlers(saved):
        for sig, old in saved.items():
            signal.signal(sig, old)

    def _recover(self):
        """After a crash, jobs may still be marked 'running'.  Put them back in the queue."""
        for job in self.db.list_jobs("running"):
            self.db.update_job(job["id"], state="pending", next_run=time.time())
            self.log("recovered job '%s' left running by a previous scheduler" % job["name"])
        for run in self.db.get_runs(statuses=["running"], limit=1000):
            self.db.update_run(run["id"], status="interrupted", end_time=time.time(),
                               message="scheduler stopped unexpectedly")

    def _shutdown(self):
        for proc in list(self.procs.values()):
            self._terminate(proc, "interrupted")
        end = time.time() + self.kill_grace + 3
        while self.procs and time.time() < end:
            self._service_processes(time.time())
            time.sleep(0.05)
        for proc in list(self.procs.values()):         # last resort
            proc.send(signal.SIGKILL)
        for proc in list(self.procs.values()):
            time.sleep(0.05)
            self._service_processes(time.time())

    # -------------------------------------------------------------- idle detection
    def is_idle(self):
        """True when nothing is running and no finite job can make further progress."""
        if any(p.status != PAUSED for p in self.procs.values()):
            return False
        for job in self.db.list_jobs("pending"):
            finite = job["schedule_type"] in ("manual", "once") or job["last_status"] == "retrying"
            if finite and job["next_run"] is not None and self._can_progress(job, set()):
                return False
        return True

    def _can_progress(self, job, seen):
        if job["id"] in seen:
            return False
        seen.add(job["id"])
        for dep in self.db.get_dependencies(job["id"]):
            recurring = dep["schedule_type"] in RECURRING
            satisfied = (dep["last_status"] == "success") if recurring else dep["state"] == "completed"
            if satisfied or dep["state"] in ("failed", "running"):
                continue
            if dep["state"] == "paused":
                return False
            if dep["state"] == "pending" and dep["next_run"] is None:
                return False                           # manual job nobody has executed
            if not self._can_progress(dep, seen):
                return False
        return True

    # ------------------------------------------------------------ 1. control from CLI
    def _apply_control(self, now):
        for proc in list(self.procs.values()):
            if proc.reason:
                continue
            job = self.db.get_job_by_id(proc.job_id)
            if job is None:
                self.log("job '%s' was deleted - terminating pid %d" % (proc.job_name, proc.pid))
                self._terminate(proc, "deleted")
            elif job["kill_requested"]:
                self.db.update_job(job["id"], kill_requested=0)
                self.log("kill requested for '%s' - terminating pid %d" % (job["name"], proc.pid))
                self._terminate(proc, "killed")
            elif job["state"] == "paused" and proc.status != PAUSED:
                self._stop(proc, PAUSED, now)
                self.log("PAUSE   '%s' (pid %d) stopped with SIGSTOP" % (job["name"], proc.pid))
            elif job["state"] == "running" and proc.status == PAUSED:
                proc.status = SUSPENDED                # goes back to the ready queue
                proc.queued_at = now
                self.log("RESUME  '%s' re-entered the ready queue" % job["name"])

    # ---------------------------------------------- 2. supervise running processes
    def _service_processes(self, now):
        for proc in list(self.procs.values()):
            proc.read_output()
            if proc.poll():
                proc.read_output()
                proc.close()
                self._finalize(proc, now)
                continue
            if (proc.reason is None and proc.timeout > 0 and proc.status == RUNNING
                    and proc.active_total(now) >= proc.timeout):
                self.log("TIMEOUT '%s' exceeded %ds - sending SIGTERM" % (proc.job_name, proc.timeout))
                self._terminate(proc, "timeout")
            if proc.term_at is not None and not proc.sigkill_sent and now - proc.term_at >= self.kill_grace:
                proc.sigkill_sent = True
                proc.send(signal.SIGKILL)
                self.log("'%s' ignored SIGTERM - sent SIGKILL" % proc.job_name)

    def _terminate(self, proc, reason):
        """Polite termination: SIGTERM (+SIGCONT so a stopped process can act on it)."""
        now = time.time()
        proc.reason = reason
        proc.term_at = now
        proc.send(signal.SIGTERM)
        proc.send(signal.SIGCONT)
        if proc.slice_started is None:
            proc.slice_started = now
        proc.status = RUNNING

    def _stop(self, proc, new_status, now):
        proc.send(signal.SIGSTOP)
        proc.active_time = proc.active_total(now)
        proc.slice_started = None
        proc.status = new_status
        if new_status == SUSPENDED:
            proc.queued_at = now

    def _cont(self, proc, now):
        proc.send(signal.SIGCONT)
        proc.slice_started = now
        proc.status = RUNNING

    # --------------------------------------------------- 3. Round Robin preemption
    def _enforce_time_slice(self, now):
        quantum = self.scheduler.time_slice
        if not quantum:
            return
        waiting = None
        for proc in list(self.procs.values()):
            if proc.status != RUNNING or proc.reason or proc.slice_elapsed(now) < quantum:
                continue
            if waiting is None:
                waiting = bool(self._ready_candidates(now))
            if waiting:
                self._stop(proc, SUSPENDED, now)
                proc.preemptions += 1
                self.log("PREEMPT '%s' used its %gs quantum -> SIGSTOP, back of the ready queue"
                         % (proc.job_name, quantum))

    # --------------------------------------------------------- dependency handling
    def _dependency_status(self, job):
        """Return ('ok', ready_time) | ('wait', None) | ('failed', name_of_failed_dependency)."""
        status, ready_time = "ok", 0.0
        for dep in self.db.get_dependencies(job["id"]):
            if dep["schedule_type"] in RECURRING:
                satisfied = dep["last_status"] == "success" and dep["state"] != "running"
                failed = False
            else:
                satisfied = dep["state"] == "completed"
                failed = dep["state"] == "failed"
            if failed:
                return "failed", dep["name"]
            if satisfied:
                ready_time = max(ready_time, dep["last_run"] or 0.0)
            else:
                status = "wait"
        return (status, ready_time if status == "ok" else None)

    def _block_failed_dependents(self, now):
        for job in self.db.due_jobs(now):
            if job["id"] in self.procs:
                continue
            status, info = self._dependency_status(job)
            if status == "failed":
                self._skip_job(job, info, now)

    def _skip_job(self, job, failed_dep, now):
        message = "skipped: prerequisite job '%s' failed" % failed_dep
        self.db.add_run(job_id=job["id"], job_name=job["name"], attempt=job["retries_used"] + 1,
                        algorithm=self.scheduler.name, start_time=now, end_time=now, duration=0.0,
                        active_time=0.0, status="skipped", message=message)
        self.log("SKIP    '%s' - %s" % (job["name"], message))
        self._write_failure_report(job["name"], "skipped", message, "")
        fields = {"last_status": "skipped", "last_run": now, "retries_used": 0}
        self._finish_cycle(job, fields, "failed", now)

    # ---------------------------------------------------------------- 5. dispatch
    def _running_count(self):
        return sum(1 for p in self.procs.values() if p.status == RUNNING)

    def _ready_candidates(self, now):
        candidates = []
        for proc in self.procs.values():
            if proc.status == SUSPENDED and not proc.reason:
                job = self.db.get_job_by_id(proc.job_id)
                if job is not None:
                    candidates.append(Candidate(job, proc.queued_at or now, proc))
        for job in self.db.due_jobs(now):
            if job["id"] in self.procs:
                continue
            status, ready_time = self._dependency_status(job)
            if status == "ok":
                candidates.append(Candidate(job, max(job["next_run"], ready_time), None))
        return candidates

    def _dispatch(self, now):
        while self._running_count() < self.workers:
            candidates = self._ready_candidates(now)
            if not candidates:
                return
            chosen = self.scheduler.select(candidates, now)
            if chosen.proc is not None:
                self._cont(chosen.proc, now)
                self.log("CONTINUE '%s' (pid %d) resumed with SIGCONT" % (chosen.job["name"], chosen.proc.pid))
            else:
                self._start(chosen.job, now)

    def _start(self, job, now):
        attempt = job["retries_used"] + 1
        run_id = self.db.add_run(job_id=job["id"], job_name=job["name"], attempt=attempt,
                                 algorithm=self.scheduler.name, start_time=now, status="running")
        try:
            proc = ManagedProcess.spawn(job["command"])
        except OSError as error:
            message = "could not start process: %s" % error
            self.db.update_run(run_id, end_time=now, duration=0.0, active_time=0.0,
                               status="failed", message=message)
            self.log("ERROR   '%s' %s" % (job["name"], message))
            self._apply_outcome(job, "failed", now, message)
            return
        proc.job_id, proc.job_name, proc.run_id = job["id"], job["name"], run_id
        proc.timeout = job["timeout"]
        proc.start_time = proc.slice_started = now
        self.procs[job["id"]] = proc
        self.db.update_run(run_id, pid=proc.pid)
        fields = {"state": "running", "next_run": None}
        # A regular (not retry / manual) trigger of a recurring job: compute the following occurrence.
        if (job["schedule_type"] in RECURRING and job["sched_next"] is not None
                and job["next_run"] is not None and abs(job["next_run"] - job["sched_next"]) < 1e-6):
            fields["sched_next"] = next_occurrence(job["schedule_type"], job["schedule_spec"],
                                                   now, anchor=job["sched_next"])
        self.db.update_job(job["id"], **fields)
        self.log("START   '%s' (id %d, priority %d) pid %d, attempt %d"
                 % (job["name"], job["id"], job["priority"], proc.pid, attempt))

    # ----------------------------------------------------------- finishing a run
    def _finalize(self, proc, now):
        del self.procs[proc.job_id]
        code = proc.exit_code
        active = proc.active_total(now)
        if proc.reason == "timeout":
            status, message = "timeout", "exceeded timeout of %ds of run time" % proc.timeout
        elif proc.reason in ("killed", "deleted"):
            status, message = "killed", "terminated on request (%s)" % proc.reason
        elif proc.reason == "interrupted":
            status, message = "interrupted", "scheduler was stopped while the job was running"
        elif code == 0:
            status, message = "success", "exit code 0"
        else:
            status, message = "failed", describe_exit(code)
        output = proc.output_text()
        self.db.update_run(proc.run_id, end_time=now, duration=now - proc.start_time,
                           active_time=active, preemptions=proc.preemptions, exit_code=code,
                           status=status, message=message, output=output)
        self.log("%-7s '%s' %s after %.2fs (active %.2fs, preemptions %d)"
                 % (status.upper(), proc.job_name, message, now - proc.start_time, active, proc.preemptions))
        job = self.db.get_job_by_id(proc.job_id)
        if job is not None:
            self._apply_outcome(job, status, now, message, output)

    def _apply_outcome(self, job, status, now, message, output=""):
        """Decide what happens to the job after a run: complete, retry, or fail."""
        fields = {"last_run": now}
        if status == "success":
            fields.update(last_status="success", retries_used=0)
            self._finish_cycle(job, fields, "completed", now)
        elif status == "interrupted":
            fields.update(state="pending", next_run=now)
            self.db.update_job(job["id"], **fields)
        elif status == "killed":
            fields.update(last_status="killed", retries_used=0)
            self._finish_cycle(job, fields, "failed", now)
        elif job["retries_used"] < job["max_retries"]:          # failed or timeout, retries left
            used = job["retries_used"] + 1
            fields.update(last_status="retrying", retries_used=used, state="pending",
                          next_run=now + job["retry_delay"])
            self.db.update_job(job["id"], **fields)
            self.log("RETRY   '%s' will be retried (%d of %d) in %ds"
                     % (job["name"], used, job["max_retries"], job["retry_delay"]))
        else:                                                    # retries exhausted
            fields.update(last_status="failed", retries_used=0)
            self._finish_cycle(job, fields, "failed", now)
            attempts = job["retries_used"] + 1
            self._write_failure_report(job["name"], status,
                                       "%s after %d attempt(s)" % (message, attempts), output)

    def _finish_cycle(self, job, fields, terminal_state, now):
        """End of one run cycle.  Recurring jobs wait for their next slot, others finish."""
        if job["schedule_type"] in RECURRING:
            upcoming = job["sched_next"]
            if upcoming is None or upcoming <= now:
                upcoming = next_occurrence(job["schedule_type"], job["schedule_spec"], now,
                                           anchor=job["sched_next"])
            fields.update(state="pending", next_run=upcoming, sched_next=upcoming)
        else:
            fields.update(state=terminal_state, next_run=None)
        self.db.update_job(job["id"], **fields)

    def _write_failure_report(self, name, status, message, output):
        tail = "\n".join(output.strip().splitlines()[-5:]) if output else ""
        self.log("FAILURE REPORT: job '%s' -> %s (%s)" % (name, status, message))
        if tail:
            self.log("   last output lines: " + tail.replace("\n", " | "))
        try:
            with open(self.failure_log_path, "a") as handle:
                handle.write("%s job=%s status=%s detail=%s\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), name, status, message))
                if tail:
                    handle.write("    output tail:\n        " + tail.replace("\n", "\n        ") + "\n")
        except OSError:
            pass
