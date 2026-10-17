# Viva questions and answers

## A. Project basics

**1. What does your project do?**
It is a job scheduler and task-automation system for Linux/macOS. Users register commands or scripts with priority, schedule, dependencies, retries and timeout. A scheduling engine runs them as real child processes using FCFS, Priority or Round Robin, logs every run in SQLite and reports failures.

**2. Why Python, and is it still "real" OS programming?**
Python's `os` module is a thin wrapper over the system calls: `os.fork()` = `fork(2)`, `os.execvp()` = `execvp(3)`, `os.waitpid()` = `waitpid(2)`, `os.killpg()` = `killpg(2)`, `os.setpgid()` = `setpgid(2)`. We use these directly rather than hiding them behind `subprocess`, so the behaviour is genuine process management. Python also gives SQLite and portability without a build step.

**3. What is the architecture?**
Two cooperating processes: the CLI (controller) and the engine (scheduler loop). They share a SQLite database. The engine ticks every 0.25 s: apply control requests, supervise processes, enforce the RR quantum, fail jobs with failed prerequisites, dispatch ready jobs.

**4. Why does the CLI talk to the engine through the database and not directly?**
It is simple, persistent and crash-safe, and needs no sockets. State survives restarts. The trade-off is polling latency (up to one tick).

## B. Processes

**5. What happens when a job starts?**
`os.pipe()` creates a pipe, `os.fork()` creates a child. The child calls `setpgid(0,0)` (own process group), `dup2`s the pipe onto stdout/stderr, `/dev/null` onto stdin and calls `execvp("/bin/sh", ["sh","-c",command])`. The parent closes the write end, makes the read end non-blocking and records the pid.

**6. What does `fork()` return and why is that useful?**
It returns 0 in the child, the child's pid in the parent and -1 on error. The return value lets one code path split into parent and child behaviour.

**7. What does `exec` do?** It replaces the child's memory image with a new program (here the shell); the pid stays the same, open file descriptors (with the redirections) are inherited.

**8. How do you avoid zombie processes?**
The engine calls `waitpid(pid, WNOHANG)` every tick for each child, which reaps terminated children and returns their status. A zombie is a terminated child whose parent has not yet called `wait`.

**9. How is the exit status decoded?**
`WIFEXITED` -> `WEXITSTATUS` gives the code; `WIFSIGNALED` -> `WTERMSIG` gives the killing signal, stored as a negative number (-15 = SIGTERM, -9 = SIGKILL). 127 means "command not found" from the shell.

**10. Why a process group?**
A job like `sh -c "a; b"` creates several processes. `killpg()` delivers a signal to all members of the group, so a timeout or kill leaves no orphans. Each child calls `setpgid(0,0)`, and the parent calls `setpgid(pid,pid)` too to avoid a race.

**11. How do you capture output without blocking the scheduler?**
Child stdout+stderr go to a pipe; the read end is non-blocking (`os.set_blocking(fd, False)`). Each tick we read whatever is available; `BlockingIOError` means "nothing yet"; an empty read means EOF. Output is capped at 64 KB per run.

**12. How are timeouts and kills done?**
`SIGTERM` first (lets the program clean up). If the process is still alive after 2 s the engine sends `SIGKILL`, which cannot be caught or ignored. A stopped process is also sent `SIGCONT` so it can handle `SIGTERM`. Tested with a script that ignores `SIGTERM`.

**13. Difference between SIGTERM, SIGKILL, SIGSTOP, SIGCONT, SIGINT?**
SIGTERM: polite termination request (catchable). SIGKILL: forced termination (not catchable). SIGSTOP: freezes a process (not catchable). SIGCONT: continues a stopped process. SIGINT: interrupt (Ctrl+C); our engine catches it for graceful shutdown.

**14. Why are `SIGKILL` and `SIGSTOP` special?**
They cannot be caught, blocked or ignored, so the kernel always retains control over any process.

## C. Scheduling

**15. Explain FCFS. Weakness?**
Jobs run in order of arrival, non-preemptive. Weakness: the *convoy effect* - a long job makes short jobs wait; average waiting time can be high.

**16. Explain priority scheduling. Weakness and fix?**
The smallest priority number (1 = highest) runs first. Weakness: *starvation* of low-priority jobs. Fix: *aging* - we subtract `waited / aging_seconds` from the priority number so waiting jobs gradually win (`--aging`).

**17. Is your priority scheduling preemptive?**
No - a running job is never interrupted by a newly arrived higher-priority job. Preemptive priority is listed as future work.

**18. Explain Round Robin and how you implemented the time quantum.**
A FIFO ready queue and a fixed quantum. After a job has actively run for `q` seconds and another job is waiting, the engine sends `SIGSTOP` to its process group, moves it to the back of the queue, and runs the next one. When its turn returns it gets `SIGCONT`. The process is never restarted; it continues where it stopped.

**19. How does this relate to a real context switch?**
The kernel saves/restores CPU registers; we emulate at user level by suspending/resuming whole processes with signals. The concept (saving state, queueing, resuming) is the same, but the granularity is coarser (0.25 s tick).

**20. What happens if the quantum is very small or very large?**
Very small: too much switching overhead and slow jobs. Very large: behaves like FCFS.

**21. Why does RR only preempt when somebody is waiting?**
If the ready queue is empty, stopping the job would only waste time; a real RR scheduler also just continues the only runnable process.

**22. How do you measure RR?**
Each run records `active_time` (time actually executing), `duration` (wall clock, includes suspended time) and `preemptions`. In the demo each 2-second job shows ~2.1 s active, 3-4 s wall, 2 preemptions.

**23. Why does `cpu_burn.py` use CPU time rather than `sleep`?**
A sleeping process keeps its timer while stopped, so it would not visibly lose progress. CPU time only advances while the process executes.

**24. What are turnaround time, waiting time and response time?**
Turnaround = completion - arrival. Waiting = time in the ready queue. Response = first run - arrival. Our history table (start/end/duration) gives the data to compute them.

**25. What does `--workers N` do?**
Allows N jobs to run simultaneously (like N CPUs). With 1 worker the algorithm's order is clearly visible.

## D. Time-based automation, dependencies, failures

**26. Explain the three schedule types.**
One-time (`--at/--in`): single due time. Periodic (`--every`): fixed rate anchored to a start time; missed slots are skipped. Cron (`--cron`): five fields minute/hour/day-of-month/month/day-of-week with `*`, lists, ranges, steps and names.

**27. How do you compute the next cron time?**
`CronSchedule.next_after(ts)` starts at the next minute, walks day by day until month and day rules match, then picks the first allowed hour/minute. If both day-of-month and day-of-week are restricted a day matches when either matches (standard cron rule).

**28. How do dependencies work?**
`--depends-on a,b`. A job is ready only when all prerequisites *completed successfully*. If a prerequisite fails permanently the dependent is skipped and marked failed; this cascades.

**29. How do you prevent circular dependencies (deadlock)?**
Before saving, a depth-first search follows `depends_on` edges from the proposed prerequisites; if it reaches the job itself the request is rejected. Without this check A->B->A would wait forever.

**30. How do retries work?**
On failure or timeout, if `retries_used < max_retries`, the job returns to pending with `next_run = now + retry_delay`. Each attempt is a separate run row with its attempt number. When retries are exhausted the job fails and a failure report is written.

**31. What does a failure report contain?**
Job name, status (failed/timeout/skipped/killed), reason (exit code or signal), number of attempts and the last lines of output - printed by the engine, appended to `failures.log` and available through `jobsched.py failures`.

**32. What happens if the scheduler crashes while jobs are running?**
At the next start, jobs still marked `running` are re-queued and their run rows set to `interrupted`. The file lock (`flock`) is released automatically by the kernel when a process dies, so a new engine can start.

**33. What prevents two engines running at once?**
A non-blocking exclusive `flock()` on `engine.lock`; the second engine reports "another scheduler instance is already running".

## E. Design, safety, extension

**34. Where is data stored and why SQLite?**
`~/.jobsched/jobs.db`. SQLite is serverless, transactional, in the standard library, and supports SQL queries for history/monitoring. Tables: `jobs`, `dependencies`, `runs`.

**35. How is input validated?**
`validation.py`: names by regular expression, commands non-empty with no NUL byte and a length limit, priority 1-10, retries 0-10, durations parsed strictly, cron syntax parsed field by field, scripts must exist and be executable. SQL uses parameterised queries and a column whitelist, so there is no SQL injection through user input.

**36. Security considerations?**
Commands run through `/bin/sh -c` with the user's own privileges - anyone who can add jobs can run any command as that user (same as cron). No sandboxing; resource limits (`setrlimit`/cgroups) are future work.

**37. How would you extend the system?**
Run as a daemon/systemd service, preemptive priority and SJF, exponential back-off, resource limits, notifications, a web dashboard, multi-user permissions.

**38. Which features are *not* implemented?**
Daemonisation, preemptive priority, back-off, resource limits, catch-up of missed runs, Windows support, DST-aware cron.

**39. How did you test it?**
47 automated tests; the integration tests start real processes and verify ordering, preemption, retries, timeouts (including SIGKILL escalation), pause/resume (checking the process really enters stopped state via `ps`), kill and crash recovery. `demo.sh` exercises everything end to end.

**40. Why does `cron` alone not solve this problem?**
Cron has no dependencies, priorities, retries, timeouts, pause/resume, process-level monitoring or central history; our scheduler adds those while also demonstrating the OS concepts.
