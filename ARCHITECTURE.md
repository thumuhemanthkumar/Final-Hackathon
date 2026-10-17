# Architecture, Algorithms and OS Concepts

## 1. Choice of implementation language

| Criterion | Python 3 | C |
|---|---|---|
| Direct access to `fork/exec/waitpid/kill/setpgid` | Yes - `os.fork`, `os.execvp`, `os.waitpid`, `os.killpg`, `os.setpgid` are thin wrappers over the system calls | Yes |
| SQLite, cron parsing, tables, argparse | Standard library | Needs external libraries / lots of code |
| Portability Ubuntu <-> macOS | Same code, no build step | Needs compiling, `/proc` and Linux-only calls differ |
| Development / demo risk | Low, readable | Higher (manual memory management) |

**Decision: Python 3 (standard library only)** - but we deliberately use `os.fork()`/`os.execvp()` and signals
instead of high-level `subprocess` convenience so that the real operating-system mechanisms are visible in
the code and can be explained in the viva.

## 2. Component view

```
 +----------------------+        SQLite file (jobs.db)         +--------------------------------+
 |  CLI  (cli.py)       |  <---------------------------------> |  Scheduling engine (engine.py) |
 |  add list edit ...   |   jobs / dependencies / runs tables  |  loop every 0.25 s             |
 |  pause resume kill   |                                      |                                |
 +----------------------+                                      |   +------------------------+   |
        ^   validation.py                                      |   | algorithms.py          |   |
        |   display.py                                         |   | FCFS | PRIORITY | RR   |   |
        |                                                      |   +------------------------+   |
        |                                                      |   +------------------------+   |
        |                                                      |   | process.py             |   |
        |                                                      |   | fork / exec / waitpid  |   |
        |                                                      |   | killpg / pipes         |   |
        |                                                      |   +-----------+------------+   |
        |                                                      +---------------|----------------+
        |                                                                      v
        |                                                         child processes (sh -c "job")
        |                                                         each in its own process group
```

The CLI and the engine are **separate processes** that only share the database. This is the classic
"controller + daemon" design (like `crontab`/`cron`, `systemctl`/`systemd`).

## 3. One scheduler tick

```
tick (every 0.25 s)
 |
 +-- 1. apply_control        pause/resume/kill/delete requested through the DB -> SIGSTOP / SIGCONT / SIGTERM
 +-- 2. service_processes    read child output (non-blocking), waitpid(WNOHANG) to reap, timeouts, SIGKILL escalation
 +-- 3. enforce_time_slice   Round Robin: quantum used up and someone waiting?  -> SIGSTOP + back of queue
 +-- 4. block_failed_deps    due job whose prerequisite failed permanently -> mark skipped/failed
 +-- 5. dispatch             while a worker slot is free: ready queue -> algorithm picks one -> fork+exec or SIGCONT
```

**Ready queue.** A job is *ready* when `state = pending`, `next_run <= now` and every prerequisite is
satisfied. A Round-Robin-preempted process is also in the ready queue (as a *suspended* candidate).

## 4. Job life cycle

```
            add                 due + deps ok + free slot            exit 0
 (new) ----------> PENDING -----------------------------> RUNNING ------------> COMPLETED   (manual / one-time)
                    ^  ^  |                                 |  |                 or back to PENDING (periodic / cron)
        resume      |  |  | pause                     pause |  | exit != 0 / timeout
                    |  |  v                                 v  v
                    | PAUSED <----------------------------+  retries left? --yes--> PENDING (next_run = now + delay)
                    |                                          | no
                    +---- execute -------------------------> FAILED  (prerequisite failed -> dependents "skipped")
```

## 5. Scheduling algorithms

All three share one interface: given the list of ready candidates, return the one that runs next
(`algorithms.py`, a *sort key*; lowest key wins). Concurrency is controlled with `--workers N`
(default 1, which makes the order easy to observe).

### 5.1 FCFS - First-Come First-Served
* **Rule:** run in order of arrival time in the ready queue (time the job became due and its prerequisites were done), ties by job id.
* **Preemptive:** no. A job keeps the worker until it ends.
* **Pros:** simple, fair in arrival order, no starvation. **Cons:** convoy effect - one long job delays all short ones; ignores importance.

### 5.2 Priority scheduling
* **Rule:** the job with the smallest priority number (1 = highest) goes first; ties by arrival time.
* **Preemptive:** no (a running job is not interrupted by a higher-priority arrival) - listed as future work.
* **Aging (`--aging S`):** effective priority = `priority - waited/S`. A job that waits long gradually beats newer high-priority jobs, which
  prevents **starvation** (the classic weakness of priority scheduling).

### 5.3 Round Robin (time-sliced)
* **Rule:** FIFO ready queue + fixed **time quantum** `q` (`--quantum`).
* **How preemption really works:** when a running process has used `q` seconds *and another job is waiting*, the engine sends
  `SIGSTOP` to its process group (the process is frozen but stays in memory), puts it at the back of the queue, and dispatches the next candidate.
  Later `SIGCONT` resumes it exactly where it stopped. (This is the same signal pair a shell uses for Ctrl+Z / `fg`.)
* **Accounting:** `active_time` counts only the time spent executing; `duration` (wall clock) includes the time spent suspended.
  `preemptions` counts how often the job was stopped. Timeouts use `active_time`.
* **Pros:** good response time, fair sharing. **Cons:** context-switch overhead; with a quantum that is too small everything slows down, too large degenerates to FCFS.

Worked example from `demo.sh` (two jobs, 2 s CPU each, q = 1 s): X(1s) -> Y(1s) -> X(1s, done) -> Y(1s, done);
each job: active 2.1 s, wall 3-4 s, 2 preemptions.

## 6. Time-based automation

| Type | Option | How the next run is computed |
|---|---|---|
| Manual | none | only on `execute` |
| One-time | `--at` / `--in` | single `next_run`; job ends `completed` |
| Periodic | `--every 30s` | fixed rate: `anchor + k * interval`; missed slots are skipped (no burst of catch-up runs) |
| Cron | `--cron "m h dom mon dow"` | `CronSchedule.next_after(now)`: walks days, then hours, then minutes; day-of-month/day-of-week use standard OR rule |

A recurring job stores `sched_next` (the next *regular* slot) separately from `next_run` (the time it is eligible *now*), so retries and
manual `execute` never disturb the regular schedule.

## 7. Process management (the heart of the project)

`ManagedProcess.spawn()` in `process.py`:

```
parent                                     child (after fork)
------                                     ------------------
pipe() -> (read_fd, write_fd)
fork() ---------------------------------> setpgid(0, 0)            # own process group (pgid = child pid)
                                           dup2(write_fd, 1 and 2)  # stdout+stderr -> pipe
                                           dup2(/dev/null, 0)       # stdin
                                           execvp("/bin/sh", ["sh", "-c", command])   # replace image
close(write_fd)
setpgid(pid, pid)       # avoids a race
set_blocking(read_fd, False)
```

| Mechanism | Where | Why |
|---|---|---|
| `fork()` + `exec()` | `process.py` | create the child and load the job's program |
| process group (`setpgid`) + `killpg` | `process.py` | one signal reaches the shell **and everything it started** (no orphans) |
| `waitpid(pid, WNOHANG)` | `poll()` | non-blocking reaping; no zombies; decodes `WIFEXITED/WEXITSTATUS/WIFSIGNALED/WTERMSIG` |
| `pipe()` + non-blocking `read` | `read_output()` | capture stdout/stderr without blocking the scheduler loop |
| `SIGTERM` -> grace 2 s -> `SIGKILL` | `engine._terminate`, `_service_processes` | polite, then forced termination (timeouts, kill, shutdown) |
| `SIGSTOP` / `SIGCONT` | `engine._stop`, `_cont` | pause/resume and Round Robin time slicing |
| `flock()` on `engine.lock` | `engine._acquire_lock` | only one engine per data dir; lock is released automatically if the engine dies |
| signal handlers (`SIGINT/SIGTERM`) | `engine.run` | graceful shutdown: children terminated, runs recorded as `interrupted`, jobs re-queued |

Exit codes are stored as POSIX does: `0` = success, `1..255` = exit status, **negative N = killed by signal N** (`-15` SIGTERM, `-9` SIGKILL), `127` = command not found.

## 8. Failure handling

1. Run ends with non-zero exit code, or is killed by the **timeout** (`active_time >= timeout` -> `SIGTERM`, then `SIGKILL` after 2 s).
2. If `retries_used < max_retries`: job goes back to `pending` with `next_run = now + retry_delay` (status `retrying`).
3. Otherwise the job is **failed** permanently: a *failure report* is printed, appended to `failures.log` (with the last output lines) and shown by `jobsched.py failures`.
4. Dependents of a permanently failed job are **skipped** (run row with status `skipped`) - the failure cascades along the chain.
5. Crash safety: on engine start, jobs left `running` by a crashed engine are re-queued and their runs marked `interrupted`.

## 9. Database schema (SQLite)

| Table | Important columns |
|---|---|
| `jobs` | `id, name (unique), command, description, priority, schedule_type, schedule_spec, state, next_run, sched_next, max_retries, retry_delay, retries_used, timeout, last_status, last_run` |
| `dependencies` | `job_id, depends_on` (cycle-checked before insert) |
| `runs` | `id, job_id, job_name, attempt, algorithm, pid, start_time, end_time, duration, active_time, preemptions, exit_code, status, message, output` |

`runs` is append-only history and survives deletion of a job.

## 10. Operating-system concepts demonstrated

| Concept | Demonstrated by |
|---|---|
| Process creation & program loading | `fork` + `exec` |
| Process states (ready, running, stopped/blocked, terminated, zombie) | job states; `SIGSTOP`'d processes; `waitpid` reaping |
| CPU scheduling (FCFS, priority, RR, aging, starvation, convoy effect, quantum) | `algorithms.py`, demo sections 2-4 |
| Context switching (user-space emulation by stopping/continuing processes) | Round Robin preemption |
| Inter-process communication | pipes (child -> scheduler), SQLite file (CLI <-> engine), signals |
| Signals | SIGTERM, SIGKILL, SIGSTOP, SIGCONT, SIGINT |
| Process groups / sessions, job control | `setpgid`, `killpg` |
| Synchronisation / mutual exclusion | `flock` engine lock; SQLite locking |
| File descriptors, redirection | `dup2`, `/dev/null`, non-blocking I/O |
| Daemon-style design, cron | periodic/cron scheduling, controller + engine split |
| Deadlock avoidance | dependency graph cycle detection |
