"""End-to-end tests of the command-line interface (add / edit / pause / resume / delete ...)."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

from tests import helpers  # noqa: F401
from jobscheduler.cli import main


class CLITests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="jobsched_cli_")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(["--home", self.home] + list(argv))
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def test_add_list_show(self):
        code, out, _ = self.cli("add", "j1", "--cmd", "echo hi", "--priority", "2", "--desc", "demo job")
        self.assertEqual(code, 0)
        self.assertIn("added job #1", out)
        _, out, _ = self.cli("list")
        self.assertIn("j1", out)
        self.assertIn("demo job", out)
        _, out, _ = self.cli("show", "j1")
        self.assertIn("echo hi", out)

    def test_validation_errors(self):
        for argv in (["add", "bad name", "--cmd", "x"],
                     ["add", "j", "--cmd", "x", "--priority", "99"],
                     ["add", "j", "--cmd", "x", "--every", "soon"],
                     ["add", "j", "--cmd", "x", "--cron", "* * *"],
                     ["add", "j"],
                     ["add", "j", "--cmd", "x", "--depends-on", "nobody"],
                     ["add", "j", "--script", "/no/such/script.sh"]):
            code, _, err = self.cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("error:", err)

    def test_duplicate_name_rejected(self):
        self.cli("add", "dup", "--cmd", "true")
        code, _, err = self.cli("add", "dup", "--cmd", "true")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_pause_resume_edit_delete(self):
        self.cli("add", "j", "--cmd", "echo a", "--every", "1h")
        self.assertEqual(self.cli("pause", "j")[0], 0)
        self.assertIn("paused", self.cli("list", "--state", "paused")[1])
        self.assertEqual(self.cli("pause", "j")[0], 2)                 # already paused
        self.assertEqual(self.cli("resume", "j")[0], 0)
        self.assertEqual(self.cli("edit", "j", "--priority", "1", "--cmd", "echo b", "--retries", "2")[0], 0)
        _, out, _ = self.cli("show", "j")
        self.assertIn("echo b", out)
        self.assertIn("priority    : 1", out)
        self.assertEqual(self.cli("edit", "j")[0], 2)                  # nothing to change
        self.assertEqual(self.cli("delete", "j")[0], 0)
        self.assertEqual(self.cli("show", "j")[0], 2)

    def test_dependency_rules(self):
        self.cli("add", "a", "--cmd", "true")
        self.cli("add", "b", "--cmd", "true", "--depends-on", "a")
        code, _, err = self.cli("edit", "a", "--depends-on", "b")      # a -> b -> a
        self.assertEqual(code, 2)
        self.assertIn("cycle", err)
        code, _, err = self.cli("delete", "a")                          # b needs it
        self.assertEqual(code, 2)
        self.assertEqual(self.cli("delete", "a", "--force")[0], 0)

    def test_execute_and_start_end_to_end(self):
        self.cli("add", "hello", "--cmd", "echo hello-world")
        self.cli("execute", "hello")
        code, out, _ = self.cli("start", "--until-idle", "--tick", "0.05")
        self.assertEqual(code, 0)
        self.assertIn("SUCCESS", out)
        _, out, _ = self.cli("history")
        self.assertIn("success", out)
        _, out, _ = self.cli("output", "1")
        self.assertIn("hello-world", out)
        _, out, _ = self.cli("status")
        self.assertIn("COMPLETED (1)", out)

    def test_scheduling_options(self):
        self.assertIn("cron", self.cli("add", "c", "--cmd", "true", "--cron", "*/5 * * * *")[1])
        self.assertIn("every 30s", self.cli("add", "p", "--cmd", "true", "--every", "30s")[1])
        self.assertEqual(self.cli("add", "o", "--cmd", "true", "--in", "10m")[0], 0)
        self.assertEqual(self.cli("add", "o2", "--cmd", "true", "--at", "2099-01-01 00:00")[0], 0)

    def test_load_demo_jobs(self):
        code, out, _ = self.cli("load-demo")
        self.assertEqual(code, 0)
        for name in ("backup-docs", "disk-usage-check", "write-timestamp"):
            self.assertIn(name, out)


if __name__ == "__main__":
    unittest.main()
