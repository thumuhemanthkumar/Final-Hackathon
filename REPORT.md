# Linux Job Scheduler and Task Automation System

**Course:** Operating Systems / System Programming - University project report
**Team:** P. Prajwal (2520080017), Kavi Krishna (2520080021), T. Hemanth (2520090074)
**Platform:** Ubuntu Linux (also runs on macOS) | **Language:** Python 3 (standard library) | **Storage:** SQLite

---

## 1. Abstract

Operating systems must decide which task runs next, for how long, and what to do when a task fails. This project
implements a *Linux Job Scheduler and Task Automation System* that makes these decisions visible. Users register shell
commands or scripts as jobs, with priorities, schedules (one-time, periodic or cron-style), prerequisites, retry
counts and timeouts. A scheduling engine selects ready jobs using **First-Come First-Served, Priority (with aging)
or Round Robin** policies and executes each job as a real Linux child process created with `fork()` and `exec()`.
The engine supervises the processes with `waitpid()`, process groups and signals (`SIGTERM`, `SIGKILL`, `SIGSTOP`,
`SIGCONT`); Round Robin time slicing is implemented by genuinely stopping and continuing processes. Dependencies,
retries, timeouts and failure reports are handled automatically. Job definitions and a complete execution history
(start time, end time, duration, exit status and captured output) are stored in SQLite. A command-line interface
supports adding, listing, executing, pausing, resuming, editing, killing and deleting jobs, and a monitoring
dashboard shows pending, running, paused, completed and failed jobs. The system uses only the Python standard
library and is verified by 47 automated tests that run real processes.

## 2. Problem statement

System administrators and developers repeatedly run tasks - backups, disk-usage checks, log rotation, builds - at
particular times, in a particular order, and with particular importance. Doing this by hand is error-prone, and
plain `cron` offers neither dependency management, priorities, retries, timeouts, pause/resume, nor a unified
history. At the same time, students need a concrete way to see operating-system concepts - process creation, signals,
CPU-scheduling policies - working together in one system. The problem is to design and implement a Linux-based job
scheduler that automates tasks reliably while demonstrating real OS process-management and scheduling mechanisms.

## 3. Objectives

1. Allow users to register jobs (command or script, name, description, priority) and manage them (list, edit, delete, pause, resume, execute).
2. Implement and compare three scheduling algorithms: FCFS, Priority (with aging) and Round Robin.
3. Support time-based automation: one-time, periodic and cron-style schedules.
4. Create and manage Linux child processes: execution, monitoring, output capture, termination, process groups.
5. Execute jobs in dependency order and prevent invalid (cyclic) dependencies.
6. Provide fault tolerance: retries, execution timeouts, failure propagation and failure reports.
7. Monitor job states and log start time, end time, duration, exit status and output for every run.
8. Persist all data in SQLite so that state survives restarts.
9. Provide demonstration tasks (backup, disk-usage check, timestamp writer) and a test suite.

## 4. Methodology

**4.1 Analysis and design.** The system is split into a *controller* (command-line interface) and an *engine*
(scheduler loop) that communicate through a SQLite database. Modules: `cli`, `engine`, `process`, `algorithms`,
`cron`, `schedule`, `db`, `validation`, `display`. Python 3 was chosen because it exposes the POSIX system calls
(`os.fork`, `os.execvp`, `os.waitpid`, `os.killpg`) directly, has SQLite built in, and runs unchanged on Ubuntu and macOS.

**4.2 Process management.** Each job is started with `fork()`; the child calls `setpgid()` to lead its own process
group, redirects stdout/stderr into a pipe, and calls `exec` to run `/bin/sh -c <command>`. The parent reads the pipe
without blocking, reaps the child with `waitpid(WNOHANG)`, and signals the whole group with `killpg()`.

**4.3 Scheduling.** The engine runs a loop every 0.25 s: apply user control requests, supervise processes (output, exit,
timeouts), enforce the Round Robin quantum, fail jobs whose prerequisites failed, and dispatch ready jobs into free
worker slots. The algorithm is a pluggable sort key over the ready queue.

**4.4 Time-based automation.** One-time jobs store a single due time. Periodic jobs use a fixed-rate schedule. Cron
jobs are parsed by a custom five-field cron parser that computes the next matching minute.

**4.5 Reliability.** Failures trigger retries after a delay; when retries are exhausted the job fails permanently,
a report is generated and dependents are skipped. Timeouts escalate from `SIGTERM` to `SIGKILL`. On start-up the engine
recovers jobs left running by a crash; a file lock prevents two engines from running together.

**4.6 Verification.** 47 automated tests (unit tests for cron, validation and algorithms; integration tests that
start real processes) cover scheduling order, priorities, Round Robin preemption, dependencies, retries,
timeouts, SIGKILL escalation, pause/resume with real signals, kill, crash recovery and the CLI. A scripted demo
(`demo.sh`) exercises every feature end to end.

## 5. Implementation summary

| Requirement | Implemented | Key code |
|---|---|---|
| Job creation | yes | `cli.cmd_add` |
| FCFS / Priority / Round Robin | yes | `algorithms.py`, `engine._dispatch`, `engine._enforce_time_slice` |
| One-time / periodic / cron | yes | `cron.py`, `schedule.py` |
| Process management | yes | `process.py` |
| Priorities and aging | yes | `algorithms.PriorityScheduler` |
| Dependencies | yes | `engine._dependency_status`, `db.creates_cycle` |
| Retries, timeouts, failure reports | yes | `engine._apply_outcome`, `_write_failure_report` |
| Monitoring | yes | `cli.render_status` |
| Logging | yes | `runs` table, `scheduler.log`, `failures.log` |
| CLI | yes | `cli.py` |
| Persistence | yes | `db.py` |
| Demonstration | yes | `examples/`, `demo.sh` |

### Results (from the demo, `docs/SAMPLE_OUTPUT.txt`)
* **FCFS:** jobs queued A, B, C ran A, B, C. **Priority:** the same jobs ran B(1), C(3), A(5).
* **Round Robin (q = 1 s):** two 2-second CPU jobs were each preempted twice; each used about 2.1 s of CPU but took 3-4 s of wall-clock time.
* **Dependencies:** `deploy`, `test`, `build` queued in reverse order ran `build` -> `test` -> `deploy`.
* **Failure cascade:** `fetch-data` failed (exit 7); `process-data` and `publish-data` were skipped.
* **Retry:** a flaky job failed twice and succeeded on attempt 3. **Timeout:** `sleep 30` with a 2 s limit was terminated by `SIGTERM`.
* **Pause/resume/kill:** a running job was frozen with `SIGSTOP`, continued with `SIGCONT`, and killed; wall time 6.3 s versus 4.2 s active time.

## 6. Limitations and future enhancements

*Limitations:* the engine runs in the foreground (not a service); priority scheduling is non-preemptive; missed runs are skipped rather than caught
up; cron uses local time without special daylight-saving handling; jobs run with the user's privileges without sandboxing; not supported on Windows.

*Future enhancements (not implemented):* background daemon / systemd unit; preemptive priority scheduling and shortest-job-first;
exponential back-off for retries; resource limits (`setrlimit`, cgroups); multi-user support; e-mail/Slack notifications; web dashboard; distributed execution.

## 7. Conclusion

The project delivers a complete, working job scheduler and automation tool that combines real Linux process management
with classic CPU-scheduling theory. FCFS, Priority and Round Robin behave as the theory predicts and can be compared directly on the same jobs.
Dependencies, retries, timeouts and failure propagation make automation reliable, while SQLite persistence and detailed logs make every
execution auditable. Using `fork`/`exec`, process groups and signals shows concretely how an operating system creates, suspends, resumes and terminates
processes. The result is both a practical utility and a teaching tool for core Operating Systems concepts, and it is extensible towards a production-grade
scheduler.

## 8. References
1. Silberschatz, Galvin, Gagne - *Operating System Concepts* (process management, CPU scheduling chapters).
2. W. R. Stevens, S. Rago - *Advanced Programming in the UNIX Environment* (fork, exec, wait, signals, process groups).
3. Linux man pages: `fork(2)`, `execve(2)`, `waitpid(2)`, `kill(2)`, `setpgid(2)`, `signal(7)`, `flock(2)`, `crontab(5)`.
4. Python documentation: `os`, `signal`, `sqlite3`, `fcntl` modules.
