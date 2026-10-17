# Linux Job Scheduler and Task Automation System

A job scheduler and task-automation tool written in **pure Python 3 (standard library only)** that
uses real POSIX process-management mechanisms: `fork()`, `exec()`, `waitpid()`, process groups and
signals (`SIGTERM`, `SIGKILL`, `SIGSTOP`, `SIGCONT`). Runs on **Ubuntu/Linux** and **macOS**.

* No external dependencies, no `pip install`.
* Jobs, dependencies and execution history are stored in **SQLite** (`jobs.db`).
* Three scheduling algorithms: **FCFS**, **Priority** (with optional aging), **Round Robin** (real time slicing).

## 1. Project structure

```
linux-job-scheduler/
├── jobsched.py                 # entry point:  python3 jobsched.py <command>
├── demo.sh                     # guided demo of every feature (~1 minute)
├── jobscheduler/               # the package
│   ├── cli.py                  # command-line interface + interactive shell
│   ├── engine.py               # scheduling loop, failure policy, dependency handling
│   ├── process.py              # fork/exec/waitpid/signals wrapper (ManagedProcess)
│   ├── algorithms.py           # FCFS, Priority (+aging), Round Robin
│   ├── cron.py                 # cron-expression parser
│   ├── schedule.py             # next-occurrence calculation for periodic / cron jobs
│   ├── db.py                   # SQLite schema and queries
│   ├── validation.py           # input validation helpers
│   └── display.py              # tables, time and duration formatting
├── examples/
│   ├── scripts/                # backup.sh, disk_usage.sh, timestamp.sh, flaky.sh, cpu_burn.py
│   └── sample_data/            # files archived by the backup demo
├── tests/                      # 47 automated tests (unit + integration with real processes)
└── docs/
    ├── ARCHITECTURE.md         # architecture, algorithms, OS concepts
    ├── REPORT.md               # abstract, problem statement, objectives, methodology, conclusion
    ├── VIVA.md                 # viva questions and answers
    └── SAMPLE_OUTPUT.txt       # real output captured from demo.sh
```

## 2. Installation

Requires **Python 3.8 or newer**. Nothing else.

**Ubuntu / Debian**
```bash
sudo apt update
sudo apt install -y python3
python3 --version
```

**macOS** (Terminal)
```bash
python3 --version            # if missing:  xcode-select --install   (or: brew install python)
```

Get the project (unzip or copy the folder), then:
```bash
cd linux-job-scheduler
chmod +x jobsched.py demo.sh examples/scripts/*       # only needed if permissions were lost
python3 jobsched.py --help
```

Nothing needs compiling. Data is kept in `~/.jobsched/` (override with `--home DIR` or the
`JOBSCHED_HOME` environment variable).

## 3. How it works in one minute

* You **register** jobs (`add`). They are stored in SQLite.
* You **start the scheduler engine** (`start`). It is a normal foreground process; leave it running in
  one terminal. It picks jobs that are due, chooses one with the selected algorithm, `fork()`s a child,
  `exec`s `/bin/sh -c "<command>"`, captures the output, and records the result.
* In **another terminal** you can `add`, `list`, `pause`, `resume`, `edit`, `delete`, `execute`, `kill`,
  `status`... The CLI and the engine communicate through the database.

```bash
# Terminal 1                                   # Terminal 2
python3 jobsched.py start --algorithm priority  python3 jobsched.py add hi --cmd "echo hi" --every 10s
```

## 4. Quick start

```bash
./demo.sh                                       # watch every feature run
python3 -m unittest discover -s tests -t .      # run the 47 tests (about 20 seconds)
```

## 5. Command reference

| Command | Purpose |
|---|---|
| `add NAME --cmd "..." \| --script FILE [options]` | register a job |
| `list [--state S]` | table of jobs |
| `status [--watch [SEC]]` | dashboard: pending / running / paused / completed / failed |
| `show JOB` | all details + recent runs |
| `edit JOB [options]` | change command, priority, schedule, retries, timeout, dependencies, name |
| `delete JOB [--force]` | remove a job (history is kept) |
| `pause JOB` / `resume JOB` | hold a job; a running process is frozen with `SIGSTOP` and continued with `SIGCONT` |
| `execute JOB` | queue a job for immediate execution |
| `kill JOB` | terminate a running job (`SIGTERM`, then `SIGKILL` after 2 s) |
| `start [--algorithm fcfs\|priority\|rr] [--quantum S] [--workers N] [--aging S] [--until-idle] [--duration S]` | run the scheduler |
| `history [JOB] [--status S] [--limit N]` | execution history |
| `output RUN_ID` | captured stdout/stderr of one run |
| `failures [--limit N]` | failure report |
| `load-demo` | register the sample automation jobs |
| `shell` | interactive prompt (`jobsched>`) |

`add` / `edit` options:

| Option | Meaning |
|---|---|
| `--cmd "CMD"` / `--script PATH` | what to run (script must exist and be executable) |
| `--desc TEXT` | description |
| `--priority 1..10` | **1 = highest**, 10 = lowest (default 5) |
| `--every 30s\|5m\|2h\|1d` | periodic job (fixed rate) |
| `--cron "m h dom mon dow"` | cron job: `*`, lists `1,5`, ranges `1-5`, steps `*/10`, names `mon-fri`, `@daily`... |
| `--at "YYYY-MM-DD HH:MM[:SS]"` or `--at HH:MM` | one-time run (or first run of an `--every` job) |
| `--in 90s\|10m` | one-time run after a delay |
| *(no schedule option)* | **manual** job, runs only when you `execute` it |
| `--retries N --retry-delay 5s` | retry failed runs |
| `--timeout 30s` | kill the job after this much *run time* |
| `--depends-on a,b` | run only after jobs `a` and `b` finished successfully |
| `--paused` | create it paused |

## 6. Example session

```bash
# --- create jobs -------------------------------------------------------
python3 jobsched.py add backup --cmd "sh examples/scripts/backup.sh" --priority 2 \
        --cron "0 2 * * *" --retries 2 --retry-delay 30s --desc "Nightly backup"
python3 jobsched.py add disk-check --script examples/scripts/disk_usage.sh --every 5m --timeout 20s
python3 jobsched.py add stamp --cmd "date >> /tmp/stamps.txt" --every 10s --priority 8
python3 jobsched.py add report --cmd "echo report" --at "2026-12-31 23:59"
python3 jobsched.py add build  --cmd "echo build; sleep 2"
python3 jobsched.py add deploy --cmd "echo deploy" --depends-on build

# --- inspect -------------------------------------------------------------
python3 jobsched.py list
python3 jobsched.py show backup

# --- run it (leave this running in its own terminal) ---------------------
python3 jobsched.py start --algorithm priority --workers 2
python3 jobsched.py start --algorithm rr --quantum 2        # time slicing
python3 jobsched.py start --until-idle                      # exit when finished

# --- manage while it runs (other terminal) --------------------------------
python3 jobsched.py execute deploy
python3 jobsched.py pause stamp
python3 jobsched.py resume stamp
python3 jobsched.py edit backup --priority 1 --retries 3
python3 jobsched.py kill long-task
python3 jobsched.py delete report
python3 jobsched.py status --watch 2

# --- logs ------------------------------------------------------------------
python3 jobsched.py history backup
python3 jobsched.py output 12
python3 jobsched.py failures
```

Sample automated tasks (backup, disk usage, timestamp) in one command:
```bash
python3 jobsched.py load-demo && python3 jobsched.py start --duration 25
cat ~/.jobsched/timestamps.log ; ls ~/.jobsched/backups
```

## 7. What is implemented

| # | Requirement | Status | Where |
|---|---|---|---|
| 1 | Job creation (command / script / name / description) | Implemented | `cli.cmd_add`, `validation.py` |
| 2 | FCFS, Priority, Round Robin | Implemented (RR uses real `SIGSTOP`/`SIGCONT` slicing) | `algorithms.py`, `engine.py` |
| 3 | One-time, periodic, cron-like | Implemented | `cron.py`, `schedule.py` |
| 4 | Process management | Implemented (`fork`, `exec`, `waitpid`, process groups, signals) | `process.py` |
| 5 | Priorities (+ aging) | Implemented | `algorithms.py` |
| 6 | Dependencies (cycle check, failure cascade) | Implemented | `engine.py`, `db.py` |
| 7 | Retries, timeouts, failure reports | Implemented (`failures` command, `failures.log`) | `engine.py` |
| 8 | Monitoring of pending/running/completed/failed/paused | Implemented (`status`, `list`) | `cli.py` |
| 9 | Logging: start, end, duration, exit status, output | Implemented (`runs` table, `scheduler.log`) | `db.py`, `engine.py` |
| 10 | CLI: add/list/execute/pause/resume/edit/delete (+kill, shell) | Implemented | `cli.py` |
| 11 | Persistent storage | Implemented (SQLite) | `db.py` |
| 12 | Demo tasks | Implemented | `examples/`, `demo.sh` |

**Not implemented (optional future enhancements):** running the engine as a background daemon /
systemd service; preemptive priority scheduling; exponential retry back-off; resource limits
(`setrlimit`, cgroups); running jobs as other users; e-mail notifications; a web dashboard;
catch-up of runs missed while the scheduler was off (missed slots are skipped);
daylight-saving-time handling in cron; Windows support.

## 8. Behaviour notes (read before the viva)

* **Priority:** 1 is the highest, 10 the lowest. Priority scheduling is non-preemptive; `--aging S` makes
  a waiting job gain one priority level per `S` seconds so low-priority jobs cannot starve.
* **Round Robin** only preempts when another job is waiting. A preempted job keeps its process; it is
  frozen with `SIGSTOP` and continued later with `SIGCONT`. Use CPU-bound jobs to see it clearly - a
  `sleep` keeps counting down while stopped.
* **Timeouts** count *active run time*, i.e. time frozen by pause/preemption is not counted.
* **Dependencies:** a job runs only when each prerequisite *completed successfully*. If a prerequisite
  fails permanently (retries exhausted) the dependent is **skipped** and marked failed, and so on down the chain.
  A prerequisite that is a recurring job counts as satisfied when its latest run succeeded.
* **Recurring jobs** return to `pending` after each run; their `LAST RESULT` shows how the previous run ended.
* **`--until-idle`** exits when no *finite* work (manual/one-time/retrying jobs) is left. Recurring jobs that are
  already due run once; jobs due later do not keep it alive. Use `--duration` for periodic demos.
* **Only one engine** may run per data directory (file lock). Pause/resume/kill of a *running* job need the
  engine to be running; if it crashed, running jobs are re-queued at the next start.
* **Output** per run is limited to 64 KB. Jobs run with *your* user privileges via `/bin/sh -c`.
* **Platforms:** developed and fully tested on Ubuntu/Linux (all 47 tests pass). The code only uses
  POSIX interfaces available on macOS (`fork`, `setpgid`, `killpg`, `fcntl.flock`, `ps -o stat=`) and the sample
  scripts avoid GNU-only options, but it was not executed on a Mac here - run
  `python3 -m unittest discover -s tests -t .` on your Mac first to confirm.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `another scheduler instance is already running` | one engine per data dir; stop the other one or use `--home` |
| `script is not executable` | `chmod +x path/to/script` |
| `permission denied` running `./demo.sh` | `chmod +x demo.sh` or run `bash demo.sh` |
| Jobs stay `pending` | the engine is not running - `python3 jobsched.py start` |
| Start over from scratch | `rm -rf ~/.jobsched` |
