"""Command-line interface: add / list / execute / pause / resume / edit / delete jobs,
start the scheduler, inspect history.  Also offers an interactive shell."""

import argparse
import fcntl
import os
import shlex
import sys
import time

from .algorithms import ALGORITHMS, make_scheduler
from .cron import CronSchedule
from .db import Database
from .display import (clip, describe_schedule, fmt_duration, fmt_next, fmt_time, paint, table)
from .engine import Engine, EngineError
from .schedule import RECURRING, next_occurrence
from .validation import (ValidationError, parse_datetime, parse_duration, validate_command,
                         validate_int, validate_name, validate_script)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_ORDER = ["running", "pending", "paused", "completed", "failed"]
FAILURE_STATUSES = ["failed", "timeout", "killed", "skipped"]


class CLIError(Exception):
    """A user-facing error that is not an input-validation problem."""


# ----------------------------------------------------------------------- helpers
def resolve_home(explicit):
    home = explicit or os.environ.get("JOBSCHED_HOME") or os.path.join(os.path.expanduser("~"), ".jobsched")
    home = os.path.abspath(home)
    os.makedirs(home, exist_ok=True)
    return home


def engine_running(home):
    """True if another process currently holds the scheduler lock."""
    path = os.path.join(home, "engine.lock")
    if not os.path.exists(path):
        return False
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


def require_job(db, ref):
    job = db.find_job(ref)
    if job is None:
        raise CLIError("no such job: %s" % ref)
    return job


def resolve_dependencies(db, text, own_id):
    """Turn 'a,b' into job ids; reject unknown names, self-dependency and cycles."""
    ids = []
    for ref in [p.strip() for p in str(text).split(",") if p.strip()]:
        dep = db.find_job(ref)
        if dep is None:
            raise ValidationError("unknown prerequisite job: %s" % ref)
        if own_id is not None and dep["id"] == own_id:
            raise ValidationError("a job cannot depend on itself")
        ids.append(dep["id"])
    if own_id is not None and db.creates_cycle(own_id, ids):
        raise ValidationError("these prerequisites would create a dependency cycle")
    return ids


def parse_schedule(args, now):
    """Return (type, spec, next_run, sched_next) or None if no schedule option was given."""
    every, cron = getattr(args, "every", None), getattr(args, "cron", None)
    at, rel = getattr(args, "at", None), getattr(args, "in_", None)
    if every and cron:
        raise ValidationError("use either --every or --cron, not both")
    if cron and (at or rel):
        raise ValidationError("--cron cannot be combined with --at / --in")
    if at and rel:
        raise ValidationError("use either --at or --in, not both")
    if not (every or cron or at or rel):
        return None
    first = None
    if at:
        first = parse_datetime(at, now)
    if rel:
        first = now + parse_duration(rel, "--in", 1)
    if every:
        seconds = parse_duration(every, "--every", 1)
        first = now if first is None else first
        return "periodic", str(seconds), first, first
    if cron:
        schedule = CronSchedule(cron)
        first = schedule.next_after(now)
        return "cron", schedule.expr, first, first
    return "once", fmt_time(first), first, None


def job_row(job, now):
    return [job["id"], job["name"], job["priority"], describe_schedule(job),
            fmt_next(job["next_run"], now) if job["state"] in ("pending", "paused") else "-",
            job["last_status"] or "-", "%d/%d" % (job["retries_used"], job["max_retries"])]


JOB_HEADERS = ["ID", "NAME", "PRI", "SCHEDULE", "NEXT RUN", "LAST RESULT", "RETRY"]


# ----------------------------------------------------------------------- commands
def cmd_add(args, db, home):
    now = time.time()
    name = validate_name(args.name)
    if bool(args.cmd) == bool(args.script):
        raise CLIError("provide exactly one of --cmd 'COMMAND' or --script PATH")
    command = validate_command(args.cmd) if args.cmd else validate_script(args.script)
    priority = validate_int(args.priority, "priority", 1, 10)
    retries = validate_int(args.retries, "--retries", 0, 10)
    retry_delay = parse_duration(args.retry_delay, "--retry-delay", 0, 86400)
    timeout = parse_duration(args.timeout, "--timeout", 0, 7 * 86400)
    schedule = parse_schedule(args, now)
    if db.get_job_by_name(name):
        raise ValidationError("job '%s' already exists" % name)
    deps = resolve_dependencies(db, args.depends_on, None) if args.depends_on else []
    stype, spec, next_run, sched_next = schedule if schedule else ("manual", None, None, None)
    if next_run is not None and next_run < now - 1:
        print("note: the scheduled time is in the past - the job runs as soon as the scheduler is started")
    job_id = db.add_job(name=name, command=command, description=args.desc or "", priority=priority,
                        schedule_type=stype, schedule_spec=spec, next_run=next_run,
                        sched_next=sched_next, max_retries=retries, retry_delay=retry_delay,
                        timeout=timeout, state="paused" if args.paused else "pending")
    db.set_dependencies(job_id, deps)
    print("added job #%d '%s' (priority %d, %s)%s" % (
        job_id, name, priority, describe_schedule({"schedule_type": stype, "schedule_spec": spec}),
        ", paused" if args.paused else ""))
    return 0


def cmd_list(args, db, home):
    if args.state and args.state not in STATE_ORDER:
        raise ValidationError("state must be one of: %s" % ", ".join(STATE_ORDER))
    jobs = db.list_jobs(args.state)
    if not jobs:
        print("no jobs found")
        return 0
    now = time.time()
    rows = [[j["id"], j["name"], j["state"], j["priority"], describe_schedule(j),
             fmt_next(j["next_run"], now) if j["state"] in ("pending", "paused") else "-",
             j["last_status"] or "-", clip(j["description"], 30)] for j in jobs]
    print(table(["ID", "NAME", "STATE", "PRI", "SCHEDULE", "NEXT RUN", "LAST", "DESCRIPTION"],
                rows, state_col=2))
    return 0


def render_status(db, home):
    now = time.time()
    counts = db.count_by_state()
    totals = db.run_totals()
    lines = ["=" * 78,
             " JOB SCHEDULER STATUS   %s   engine: %s" % (
                 fmt_time(now), "RUNNING" if engine_running(home) else "stopped"),
             "=" * 78,
             " pending %d | running %d | paused %d | completed %d | failed %d   "
             "(runs: %d total, %d ok, %d failed)" % (
                 counts["pending"], counts["running"], counts["paused"], counts["completed"],
                 counts["failed"], totals["total"], totals["success"], totals["failed"])]
    for state in STATE_ORDER:
        jobs = db.list_jobs(state)
        lines.append("")
        lines.append(paint("%s (%d)" % (state.upper(), len(jobs)), state))
        if not jobs:
            lines.append("  (none)")
            continue
        rows = []
        for job in jobs:
            row = job_row(job, now)
            if state == "running":
                runs = db.get_runs(job["id"], limit=1)
                row[4] = "since " + fmt_time(runs[0]["start_time"]) if runs else "-"
            rows.append(row)
        lines.append("\n".join("  " + l for l in table(JOB_HEADERS, rows).splitlines()))
    return "\n".join(lines)


def cmd_status(args, db, home):
    if not args.watch:
        print(render_status(db, home))
        return 0
    try:
        while True:
            print("\033[2J\033[H" + render_status(db, home) + "\n\n(Ctrl+C to leave watch mode)")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print()
    return 0


def cmd_show(args, db, home):
    job = require_job(db, args.job)
    deps = db.get_dependencies(job["id"])
    dependents = db.get_dependents(job["id"])
    print("Job #%d  %s" % (job["id"], job["name"]))
    print("  description : %s" % (job["description"] or "-"))
    print("  command     : %s" % job["command"])
    print("  state       : %s   (last result: %s)" % (job["state"], job["last_status"] or "-"))
    print("  priority    : %d (1 = highest)" % job["priority"])
    print("  schedule    : %s" % describe_schedule(job))
    print("  next run    : %s" % fmt_next(job["next_run"]))
    print("  retries     : %d used of %d (delay %ds)" % (job["retries_used"], job["max_retries"], job["retry_delay"]))
    print("  timeout     : %s" % (("%ds" % job["timeout"]) if job["timeout"] else "none"))
    print("  depends on  : %s" % (", ".join(d["name"] for d in deps) or "-"))
    print("  required by : %s" % (", ".join(d["name"] for d in dependents) or "-"))
    print("  created     : %s" % fmt_time(job["created_at"]))
    runs = db.get_runs(job["id"], limit=5)
    if runs:
        print("\nRecent runs:")
        print(runs_table(runs))
    return 0


def cmd_edit(args, db, home):
    job = require_job(db, args.job)
    if job["state"] == "running":
        raise CLIError("job '%s' is running - wait for it to finish or kill it first" % job["name"])
    now = time.time()
    changes = {}
    if args.rename:
        new_name = validate_name(args.rename)
        other = db.get_job_by_name(new_name)
        if other and other["id"] != job["id"]:
            raise ValidationError("job '%s' already exists" % new_name)
        changes["name"] = new_name
    if args.cmd and args.script:
        raise CLIError("use either --cmd or --script, not both")
    if args.cmd:
        changes["command"] = validate_command(args.cmd)
    if args.script:
        changes["command"] = validate_script(args.script)
    if args.desc is not None:
        changes["description"] = args.desc
    if args.priority is not None:
        changes["priority"] = validate_int(args.priority, "priority", 1, 10)
    if args.retries is not None:
        changes["max_retries"] = validate_int(args.retries, "--retries", 0, 10)
    if args.retry_delay is not None:
        changes["retry_delay"] = parse_duration(args.retry_delay, "--retry-delay", 0, 86400)
    if args.timeout is not None:
        changes["timeout"] = parse_duration(args.timeout, "--timeout", 0, 7 * 86400)
    if args.clear_schedule and parse_schedule(args, now):
        raise CLIError("--clear-schedule cannot be combined with other schedule options")
    schedule = parse_schedule(args, now)
    if args.clear_schedule:
        schedule = ("manual", None, None, None)
    if schedule:
        stype, spec, next_run, sched_next = schedule
        changes.update(schedule_type=stype, schedule_spec=spec, sched_next=sched_next,
                       retries_used=0)
        if job["state"] != "paused":
            changes.update(next_run=next_run, state="pending")
        else:
            changes.update(next_run=next_run)
    deps = None
    if args.depends_on is not None:
        deps = resolve_dependencies(db, args.depends_on, job["id"])
    if not changes and deps is None:
        raise CLIError("nothing to change - give at least one option (see: edit --help)")
    db.update_job(job["id"], **changes)
    if deps is not None:
        db.set_dependencies(job["id"], deps)
    print("updated job '%s'" % changes.get("name", job["name"]))
    return 0


def cmd_delete(args, db, home):
    job = require_job(db, args.job)
    dependents = db.get_dependents(job["id"])
    if job["state"] == "running" and not args.force:
        raise CLIError("job '%s' is running - use --force to terminate and delete it" % job["name"])
    if dependents and not args.force:
        raise CLIError("job '%s' is a prerequisite of: %s - use --force to delete anyway"
                       % (job["name"], ", ".join(d["name"] for d in dependents)))
    db.delete_job(job["id"])
    print("deleted job '%s' (execution history is kept)" % job["name"])
    return 0


def cmd_pause(args, db, home):
    job = require_job(db, args.job)
    if job["state"] == "paused":
        raise CLIError("job '%s' is already paused" % job["name"])
    if job["state"] not in ("pending", "running"):
        raise CLIError("job '%s' is %s - only pending or running jobs can be paused" % (job["name"], job["state"]))
    db.update_job(job["id"], state="paused")
    if job["state"] == "running":
        print("job '%s' paused - the scheduler will freeze its process with SIGSTOP" % job["name"])
    else:
        print("job '%s' paused - it will not be scheduled until resumed" % job["name"])
    return 0


def cmd_resume(args, db, home):
    job = require_job(db, args.job)
    if job["state"] != "paused":
        raise CLIError("job '%s' is not paused (state: %s)" % (job["name"], job["state"]))
    if db.get_runs(job["id"], statuses=["running"], limit=1):          # frozen live process
        db.update_job(job["id"], state="running")
        print("job '%s' resumed - its process continues with SIGCONT" % job["name"])
        return 0
    now = time.time()
    fields = {"state": "pending"}
    if job["schedule_type"] in RECURRING:
        upcoming = job["sched_next"]
        if upcoming is None or upcoming <= now:
            upcoming = next_occurrence(job["schedule_type"], job["schedule_spec"], now, job["sched_next"])
        fields.update(next_run=upcoming, sched_next=upcoming)
    elif job["next_run"] is not None:
        fields["next_run"] = max(job["next_run"], now)
    db.update_job(job["id"], **fields)
    print("job '%s' resumed" % job["name"])
    return 0


def cmd_execute(args, db, home):
    job = require_job(db, args.job)
    if job["state"] == "running":
        raise CLIError("job '%s' is already running" % job["name"])
    if job["state"] == "paused":
        raise CLIError("job '%s' is paused - resume it first" % job["name"])
    db.update_job(job["id"], state="pending", next_run=time.time(), retries_used=0, kill_requested=0)
    print("job '%s' queued for immediate execution" % job["name"])
    if not engine_running(home):
        print("hint: the scheduler is not running - start it with:  python3 jobsched.py start --until-idle")
    return 0


def cmd_kill(args, db, home):
    job = require_job(db, args.job)
    if not db.get_runs(job["id"], statuses=["running"], limit=1):
        raise CLIError("job '%s' has no running process" % job["name"])
    db.update_job(job["id"], kill_requested=1)
    print("kill requested for '%s' - the scheduler sends SIGTERM, then SIGKILL after a grace period" % job["name"])
    return 0


def runs_table(runs):
    rows = [[r["id"], r["job_name"], r["attempt"], fmt_time(r["start_time"]), fmt_time(r["end_time"]),
             fmt_duration(r["duration"]), fmt_duration(r["active_time"]),
             "-" if r["exit_code"] is None else r["exit_code"], r["status"]] for r in runs]
    return table(["RUN", "JOB", "TRY", "START", "END", "WALL", "ACTIVE", "EXIT", "STATUS"], rows, state_col=8)


def cmd_history(args, db, home):
    job_id = None
    if args.job:
        job_id = require_job(db, args.job)["id"] if db.find_job(args.job) else None
        if job_id is None:                                  # job may have been deleted: match by name
            runs = [r for r in db.get_runs(limit=100000) if r["job_name"] == args.job][:args.limit]
            print(runs_table(runs) if runs else "no runs recorded")
            return 0
    statuses = [args.status] if args.status else None
    runs = db.get_runs(job_id, statuses=statuses, limit=args.limit)
    print(runs_table(runs) if runs else "no runs recorded")
    return 0


def cmd_output(args, db, home):
    run = db.get_run(validate_int(args.run_id, "run id", 1, 10 ** 12))
    if run is None:
        raise CLIError("no such run: %s" % args.run_id)
    print("Run #%d of '%s' (attempt %d)" % (run["id"], run["job_name"], run["attempt"]))
    print("  started   : %s   ended: %s" % (fmt_time(run["start_time"]), fmt_time(run["end_time"])))
    print("  duration  : wall %s, active %s, preemptions %d" % (
        fmt_duration(run["duration"]), fmt_duration(run["active_time"]), run["preemptions"]))
    print("  pid/exit  : %s / %s   status: %s (%s)" % (run["pid"], run["exit_code"], run["status"], run["message"]))
    print("  ----- output -----")
    print(run["output"] if run["output"] else "  (no output)")
    return 0


def cmd_failures(args, db, home):
    runs = db.get_runs(statuses=FAILURE_STATUSES, limit=args.limit)
    if not runs:
        print("no failures recorded")
        return 0
    print("FAILURE REPORT (newest first)")
    print("=" * 78)
    for run in runs:
        print("run #%d  job '%s'  attempt %d  %s  [%s]" % (
            run["id"], run["job_name"], run["attempt"], fmt_time(run["start_time"]), run["status"].upper()))
        print("    reason: %s" % run["message"])
        tail = (run["output"] or "").strip().splitlines()[-3:]
        for line in tail:
            print("    | " + clip(line, 70))
    return 0


def cmd_start(args, db, home):
    scheduler = make_scheduler(args.algorithm, args.quantum, args.aging)
    workers = validate_int(args.workers, "--workers", 1, 64)
    if args.tick <= 0:
        raise ValidationError("--tick must be > 0")
    if args.duration is not None and args.duration <= 0:
        raise ValidationError("--duration must be > 0")
    engine = Engine(db, home, scheduler, workers=workers, tick=args.tick)
    print("Starting scheduler (Ctrl+C to stop gracefully)...")
    engine.run(until_idle=args.until_idle, duration=args.duration)
    return 0


def cmd_load_demo(args, db, home):
    scripts = os.path.join(PROJECT_ROOT, "examples", "scripts")
    demo = [
        dict(name="backup-docs", desc="Archive sample_data into a timestamped tar.gz",
             cmd="sh %s" % shlex.quote(os.path.join(scripts, "backup.sh")), priority=2,
             cron="0 2 * * *", retries=2, retry_delay="5"),
        dict(name="disk-usage-check", desc="Report disk usage of / (fails above 90%)",
             cmd="sh %s 90" % shlex.quote(os.path.join(scripts, "disk_usage.sh")), priority=4,
             every="20s", timeout="30"),
        dict(name="write-timestamp", desc="Append the current time to timestamps.log",
             cmd="sh %s" % shlex.quote(os.path.join(scripts, "timestamp.sh")), priority=6,
             every="10s"),
    ]
    for spec in demo:
        if db.get_job_by_name(spec["name"]):
            print("job '%s' already exists - skipped" % spec["name"])
            continue
        ns = argparse.Namespace(name=spec["name"], cmd=spec["cmd"], script=None, desc=spec["desc"],
                                priority=spec["priority"], every=spec.get("every"), cron=spec.get("cron"),
                                at=None, in_=None, retries=spec.get("retries", 0),
                                retry_delay=spec.get("retry_delay", "5"), timeout=spec.get("timeout", "0"),
                                depends_on=None, paused=False)
        cmd_add(ns, db, home)
    return 0


# ----------------------------------------------------------------------- parser
def build_parser():
    parser = argparse.ArgumentParser(
        prog="jobsched.py", description="Linux Job Scheduler and Task Automation System",
        epilog="Run 'jobsched.py <command> --help' for details.  Data lives in ~/.jobsched "
               "(override with --home or $JOBSCHED_HOME).")
    parser.add_argument("--home", help="data directory (default: $JOBSCHED_HOME or ~/.jobsched)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def schedule_options(p):
        p.add_argument("--every", metavar="INTERVAL", help="periodic job, e.g. 30s, 5m, 2h, 1d")
        p.add_argument("--cron", metavar="EXPR", help="cron job, e.g. '*/5 * * * *' or '0 9 * * mon-fri'")
        p.add_argument("--at", metavar="WHEN", help="run once at 'YYYY-MM-DD HH:MM[:SS]' or 'HH:MM' "
                                                    "(first run for --every)")
        p.add_argument("--in", dest="in_", metavar="DELAY", help="run once after a delay, e.g. 90s, 10m")

    p = sub.add_parser("add", help="register a new job")
    p.add_argument("name")
    p.add_argument("--cmd", help="shell command to run")
    p.add_argument("--script", help="path of an executable script to run")
    p.add_argument("--desc", help="description")
    p.add_argument("--priority", type=int, default=5, help="1 (highest) .. 10 (lowest), default 5")
    schedule_options(p)
    p.add_argument("--retries", type=int, default=0, help="retries after failure (0-10)")
    p.add_argument("--retry-delay", default="5", help="wait before a retry (default 5s)")
    p.add_argument("--timeout", default="0", help="kill the job after this much run time (0 = never)")
    p.add_argument("--depends-on", help="comma-separated prerequisite job names")
    p.add_argument("--paused", action="store_true", help="create the job in the paused state")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="list jobs")
    p.add_argument("--state", help="filter: pending|running|paused|completed|failed")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="dashboard: jobs grouped by state")
    p.add_argument("--watch", nargs="?", type=float, const=2.0, help="refresh every N seconds (default 2)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", help="show full details of one job")
    p.add_argument("job")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("edit", help="change a job")
    p.add_argument("job")
    p.add_argument("--rename")
    p.add_argument("--cmd")
    p.add_argument("--script")
    p.add_argument("--desc")
    p.add_argument("--priority", type=int)
    schedule_options(p)
    p.add_argument("--clear-schedule", action="store_true", help="make it a manual job")
    p.add_argument("--retries", type=int)
    p.add_argument("--retry-delay")
    p.add_argument("--timeout")
    p.add_argument("--depends-on", help="replace prerequisites (use '' to remove all)")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("delete", help="delete a job")
    p.add_argument("job")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_delete)

    for name, func, text in (("pause", cmd_pause, "pause a job (SIGSTOP if it is running)"),
                             ("resume", cmd_resume, "resume a paused job (SIGCONT if it was running)"),
                             ("execute", cmd_execute, "queue a job for immediate execution"),
                             ("kill", cmd_kill, "terminate a running job")):
        p = sub.add_parser(name, help=text)
        p.add_argument("job")
        p.set_defaults(func=func)

    p = sub.add_parser("start", help="run the scheduler engine")
    p.add_argument("--algorithm", "-a", choices=ALGORITHMS, default="fcfs")
    p.add_argument("--quantum", type=float, default=2.0, help="Round Robin time slice in seconds")
    p.add_argument("--workers", type=int, default=1, help="jobs that may run at the same time")
    p.add_argument("--aging", type=float, default=0.0, help="priority aging: seconds waited per +1 level")
    p.add_argument("--tick", type=float, default=0.25, help="scheduler loop interval in seconds")
    p.add_argument("--until-idle", action="store_true", help="exit when no finite work is left")
    p.add_argument("--duration", type=float, help="exit after N seconds")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("history", help="execution history")
    p.add_argument("job", nargs="?")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--status", help="success|failed|timeout|killed|skipped|interrupted")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("output", help="show the captured output of one run")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_output)

    p = sub.add_parser("failures", help="failure report")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_failures)

    p = sub.add_parser("load-demo", help="register the sample automation jobs")
    p.set_defaults(func=cmd_load_demo)

    p = sub.add_parser("shell", help="interactive prompt")
    p.set_defaults(func=None)
    return parser


def run_args(args):
    """Execute one parsed command.  Returns a process exit code."""
    home = resolve_home(args.home)
    db = Database(os.path.join(home, "jobs.db"))
    try:
        return args.func(args, db, home) or 0
    except (ValidationError, CLIError, EngineError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 2
    finally:
        db.close()


def interactive_shell(parser, home_arg):
    print("Linux Job Scheduler shell - type 'help' for commands, 'exit' to quit.")
    while True:
        try:
            line = input("jobsched> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("exit", "quit"):
            return 0
        if line in ("help", "?"):
            parser.print_help()
            continue
        try:
            tokens = shlex.split(line)
            if tokens and tokens[0] == "shell":
                print("already in the shell")
                continue
            prefix = ["--home", home_arg] if home_arg else []
            args = parser.parse_args(prefix + tokens)
        except ValueError as error:
            print("error: %s" % error)
            continue
        except SystemExit:
            continue
        if args.command:
            run_args(args)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "shell":
        return interactive_shell(parser, args.home)
    return run_args(args)
