"""SQLite persistence layer: job definitions, dependencies and execution history."""

import sqlite3
import time

from .validation import ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    command        TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    priority       INTEGER NOT NULL DEFAULT 5,       -- 1 = highest ... 10 = lowest
    schedule_type  TEXT NOT NULL DEFAULT 'manual',   -- manual | once | periodic | cron
    schedule_spec  TEXT,                             -- seconds (periodic) / cron string / datetime text
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending | running | paused | completed | failed
    next_run       REAL,                             -- when the job becomes eligible (NULL = not scheduled)
    sched_next     REAL,                             -- next regular occurrence (recurring jobs only)
    max_retries    INTEGER NOT NULL DEFAULT 0,
    retry_delay    INTEGER NOT NULL DEFAULT 5,
    retries_used   INTEGER NOT NULL DEFAULT 0,
    timeout        INTEGER NOT NULL DEFAULT 0,       -- seconds of active run time, 0 = unlimited
    last_status    TEXT,                             -- success | failed | retrying | killed | skipped
    last_run       REAL,
    kill_requested INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dependencies (
    job_id     INTEGER NOT NULL,
    depends_on INTEGER NOT NULL,
    PRIMARY KEY (job_id, depends_on)
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    job_name    TEXT NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 1,
    algorithm   TEXT,
    pid         INTEGER,
    start_time  REAL NOT NULL,
    end_time    REAL,
    duration    REAL,                                -- wall-clock seconds
    active_time REAL,                                -- seconds actually executing (excludes suspended time)
    preemptions INTEGER NOT NULL DEFAULT 0,
    exit_code   INTEGER,
    status      TEXT NOT NULL,                       -- running|success|failed|timeout|killed|skipped|interrupted
    message     TEXT,
    output      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_id);
"""

JOB_COLUMNS = {
    "name", "command", "description", "priority", "schedule_type", "schedule_spec", "state",
    "next_run", "sched_next", "max_retries", "retry_delay", "retries_used", "timeout",
    "last_status", "last_run", "kill_requested",
}
RUN_COLUMNS = {
    "job_id", "job_name", "attempt", "algorithm", "pid", "start_time", "end_time", "duration",
    "active_time", "preemptions", "exit_code", "status", "message", "output",
}


class Database(object):
    """Thin wrapper around sqlite3.  Rows are returned as plain dictionaries."""

    def __init__(self, path):
        self.path = path
        # isolation_level=None -> autocommit; busy timeout lets the CLI and the engine share the file.
        self.conn = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    # -- low level helpers -------------------------------------------------
    def _all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _check(fields, allowed):
        bad = set(fields) - allowed
        if bad:
            raise ValueError("unknown column(s): %s" % ", ".join(sorted(bad)))

    # -- jobs ----------------------------------------------------------------
    def add_job(self, **fields):
        self._check(fields, JOB_COLUMNS)
        now = time.time()
        fields["created_at"] = now
        fields["updated_at"] = now
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        try:
            cur = self.conn.execute("INSERT INTO jobs (%s) VALUES (%s)" % (cols, marks),
                                    list(fields.values()))
        except sqlite3.IntegrityError:
            raise ValidationError("job '%s' already exists" % fields.get("name"))
        return cur.lastrowid

    def update_job(self, job_id, **fields):
        if not fields:
            return
        self._check(fields, JOB_COLUMNS)
        fields["updated_at"] = time.time()
        sets = ", ".join("%s = ?" % c for c in fields)
        try:
            self.conn.execute("UPDATE jobs SET %s WHERE id = ?" % sets,
                              list(fields.values()) + [job_id])
        except sqlite3.IntegrityError:
            raise ValidationError("a job with that name already exists")

    def get_job_by_id(self, job_id):
        return self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def get_job_by_name(self, name):
        return self._one("SELECT * FROM jobs WHERE name = ?", (name,))

    def find_job(self, ref):
        """Look a job up by name or numeric id."""
        job = self.get_job_by_name(str(ref))
        if job is None and str(ref).isdigit():
            job = self.get_job_by_id(int(ref))
        return job

    def list_jobs(self, state=None):
        if state:
            return self._all("SELECT * FROM jobs WHERE state = ? ORDER BY id", (state,))
        return self._all("SELECT * FROM jobs ORDER BY id")

    def due_jobs(self, now):
        """Pending jobs whose scheduled time has arrived."""
        return self._all("SELECT * FROM jobs WHERE state = 'pending' AND next_run IS NOT NULL "
                         "AND next_run <= ? ORDER BY next_run, id", (now,))

    def delete_job(self, job_id):
        self.conn.execute("DELETE FROM dependencies WHERE job_id = ? OR depends_on = ?",
                          (job_id, job_id))
        self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def count_by_state(self):
        counts = {"pending": 0, "running": 0, "paused": 0, "completed": 0, "failed": 0}
        for row in self._all("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"):
            counts[row["state"]] = row["n"]
        return counts

    # -- dependencies --------------------------------------------------------
    def set_dependencies(self, job_id, dep_ids):
        self.conn.execute("DELETE FROM dependencies WHERE job_id = ?", (job_id,))
        for dep in sorted(set(dep_ids)):
            self.conn.execute("INSERT INTO dependencies (job_id, depends_on) VALUES (?, ?)",
                              (job_id, dep))

    def get_dependencies(self, job_id):
        return self._all("SELECT j.* FROM jobs j JOIN dependencies d ON d.depends_on = j.id "
                         "WHERE d.job_id = ? ORDER BY j.id", (job_id,))

    def get_dependents(self, job_id):
        return self._all("SELECT j.* FROM jobs j JOIN dependencies d ON d.job_id = j.id "
                         "WHERE d.depends_on = ? ORDER BY j.id", (job_id,))

    def creates_cycle(self, job_id, dep_ids):
        """True if making `job_id` depend on `dep_ids` would create a dependency cycle."""
        stack, seen = list(dep_ids), set()
        while stack:
            current = stack.pop()
            if current == job_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            rows = self._all("SELECT depends_on FROM dependencies WHERE job_id = ?", (current,))
            stack.extend(r["depends_on"] for r in rows)
        return False

    # -- runs (execution history) -------------------------------------------
    def add_run(self, **fields):
        self._check(fields, RUN_COLUMNS)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = self.conn.execute("INSERT INTO runs (%s) VALUES (%s)" % (cols, marks),
                                list(fields.values()))
        return cur.lastrowid

    def update_run(self, run_id, **fields):
        self._check(fields, RUN_COLUMNS)
        sets = ", ".join("%s = ?" % c for c in fields)
        self.conn.execute("UPDATE runs SET %s WHERE id = ?" % sets, list(fields.values()) + [run_id])

    def get_run(self, run_id):
        return self._one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def get_runs(self, job_id=None, statuses=None, limit=20, oldest_first=False):
        where, params = [], []
        if job_id is not None:
            where.append("job_id = ?")
            params.append(job_id)
        if statuses:
            where.append("status IN (%s)" % ", ".join("?" for _ in statuses))
            params.extend(statuses)
        sql = "SELECT * FROM runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id %s LIMIT ?" % ("ASC" if oldest_first else "DESC")
        params.append(limit)
        return self._all(sql, params)

    def run_totals(self):
        row = self._one("SELECT COUNT(*) AS total, "
                        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok, "
                        "SUM(CASE WHEN status IN ('failed','timeout','killed','skipped') THEN 1 ELSE 0 END) AS bad "
                        "FROM runs")
        return {"total": row["total"] or 0, "success": row["ok"] or 0, "failed": row["bad"] or 0}
