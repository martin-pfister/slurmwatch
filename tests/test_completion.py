import errno
import os
import subprocess
from unittest.mock import patch

from helpers import Clock, ScriptTestCase


class CompletionTests(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.path = os.path.join(self.directory, "job.out")
        self.completed_at = 8
        self.initial_state = "RUNNING"
        self.accounting_state = "COMPLETED"
        self.stdout_path = self.path
        self.unavailable_until = 0
        self.calls = []
        self.enter_patch(patch("time.monotonic", self.clock.monotonic))
        self.enter_patch(patch("time.sleep", self.clock.sleep))
        self.enter_patch(patch("signal.signal"))
        self.enter_patch(patch("shutil.which", return_value="/mock/slurm"))
        self.enter_patch(patch("subprocess.run", side_effect=self.command))

    def command(self, cmd, **kwargs):
        name = cmd[0]
        self.calls.append((self.clock.now, name))
        if name == "scontrol":
            out = "JobId=123 JobState=%s StdOut=%s\n" % (self.initial_state, self.stdout_path)
        elif name == "squeue":
            if self.clock.now < self.unavailable_until:
                return subprocess.CompletedProcess(cmd, 1, "", "Unable to contact slurm controller")
            if self.clock.now >= self.completed_at:
                return subprocess.CompletedProcess(cmd, 1, "", "Invalid job id specified")
            out = "RUNNING\n"
        elif name == "sacct":
            out = self.accounting_state + "|\n" if self.accounting_state else ""
        else:
            raise AssertionError("unexpected command: %r" % cmd)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def test_waits_for_file_and_reads_existing_content(self):
        self.clock.at(2, lambda: self.write("job.out", b"first\n"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"first\n")
        self.assertGreaterEqual(self.clock.now, self.completed_at + self.script["DRAIN"])

    def test_drain_restarts_when_late_output_arrives(self):
        self.write("job.out", b"initial\n")
        self.clock.at(15, lambda: self.write("job.out", b"late\n", "ab"))
        self.clock.at(22, lambda: self.write("job.out", b"last\n", "ab"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"initial\nlate\nlast\n")
        self.assertGreaterEqual(self.clock.now, 22 + self.script["DRAIN"])
        self.assertLess(self.clock.now, 34)
        self.assertEqual(sum(name == "sacct" for _, name in self.calls), 1)

    def test_scheduler_outage_does_not_end_following(self):
        self.write("job.out", b"initial\n")
        self.unavailable_until = 20
        self.clock.at(15, lambda: self.write("job.out", b"during outage\n", "ab"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"initial\nduring outage\n")
        self.assertGreaterEqual(self.clock.now, 20 + self.script["DRAIN"])

    def test_completed_job_without_file_exits_unsuccessfully(self):
        self.completed_at = 0
        self.assertEqual(self.script["main"](["123"]), 1)
        self.assertEqual(self.output.getvalue(), b"")
        self.assertIn("without writing", self.errors.getvalue())
        completion_seen = next(t for t, name in self.calls if name == "sacct")
        self.assertGreaterEqual(self.clock.now, completion_seen + self.script["DRAIN"])

    def test_file_appearing_just_after_completion_is_drained(self):
        self.completed_at = 0
        self.clock.at(5, lambda: self.write("job.out", b"delayed visibility\n"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"delayed visibility\n")
        self.assertGreaterEqual(self.clock.now, 5 + self.script["DRAIN"])

    def test_late_empty_file_gets_full_drain_period(self):
        self.completed_at = 0
        self.clock.at(12, lambda: self.write("job.out", b""))
        self.clock.at(18, lambda: self.write("job.out", b"final flush\n", "ab"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"final flush\n")
        self.assertGreaterEqual(self.clock.now, 18 + self.script["DRAIN"])

    def test_initial_completion_drains_without_more_slurm_queries(self):
        self.initial_state = "COMPLETED"
        self.unavailable_until = 180
        self.write("job.out", b"already done\n")
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"already done\n")
        self.assertEqual(self.clock.now, self.script["DRAIN"])
        self.assertEqual(self.calls, [(0, "scontrol")])

    def test_initial_completion_still_waits_for_missing_file(self):
        self.initial_state = "COMPLETED"
        self.clock.at(5, lambda: self.write("job.out", b"late\n"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"late\n")
        self.assertEqual(self.clock.now, 5 + self.script["DRAIN"])
        self.assertEqual(self.calls, [(0, "scontrol")])

    def test_no_stdout_exits_without_waiting(self):
        for value in ("NONE", "", os.devnull):
            with self.subTest(stdout=value):
                self.stdout_path = value
                self.assertEqual(self.script["main"](["123"]), 1)
                self.assertEqual(self.clock.now, 0)
        self.assertIn("writes no output file", self.errors.getvalue())

    def test_slurm_filename_with_brackets_is_not_globbed(self):
        self.initial_state = "COMPLETED"
        self.stdout_path = os.path.join(self.directory, "run[1].out")
        self.write("run1.out", b"unrelated\n")
        self.clock.at(5, lambda: self.write("run[1].out", b"correct\n"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"correct\n")

    def test_ctrl_c_during_startup_is_quiet(self):
        with patch("subprocess.run", side_effect=KeyboardInterrupt):
            self.assertEqual(self.script["main"](["123"]), 130)
        self.assertEqual(self.errors.getvalue(), "")

    def test_ctrl_c_while_following_is_quiet(self):
        self.write("job.out", b"hello\n")
        with patch("time.sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(self.script["main"](["123"]), 130)
        self.assertEqual(self.output.getvalue(), b"hello\n")
        self.assertNotIn("Traceback", self.errors.getvalue())

    def test_unreadable_file_is_reported_instead_of_success(self):
        self.initial_state = "COMPLETED"
        self.write("job.out", b"cannot read this\n")
        error = PermissionError(errno.EACCES, "Permission denied", self.path)
        with patch("builtins.open", side_effect=error):
            self.assertEqual(self.script["main"](["123"]), 2)
        self.assertEqual(self.output.getvalue(), b"")
        self.assertIn("Permission denied", self.errors.getvalue())
        self.assertIn(self.path, self.errors.getvalue())
        self.assertEqual(self.clock.now, 0)

    def test_read_failure_during_drain_is_not_success(self):
        self.initial_state = "COMPLETED"
        self.write("job.out", b"first\n")
        real_open = open

        def open_until_failure(*args, **kwargs):
            if self.clock.now >= 2:
                raise OSError(errno.EIO, "Input/output error", self.path)
            return real_open(*args, **kwargs)

        with patch("builtins.open", side_effect=open_until_failure):
            self.assertEqual(self.script["main"](["123"]), 2)
        self.assertEqual(self.output.getvalue(), b"first\n")
        self.assertIn("Input/output error", self.errors.getvalue())

    def test_gone_job_with_output_is_drained(self):
        self.write("job.out", b"last lines\n")
        self.accounting_state = ""
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"last lines\n")
        self.assertGreaterEqual(self.clock.now, self.completed_at + self.script["DRAIN"])

    def test_gone_job_without_output_fails_after_grace_period(self):
        self.accounting_state = ""
        self.assertEqual(self.script["main"](["123"]), 1)
        self.assertIn("without writing", self.errors.getvalue())
        self.assertGreaterEqual(self.clock.now, self.completed_at + self.script["DRAIN"])

    def test_scheduler_backoff_resets_after_output_resumes(self):
        self.completed_at = 110
        self.write("job.out", b"initial\n")
        self.clock.at(80, lambda: self.write("job.out", b"resumed\n", "ab"))
        self.assertEqual(self.script["main"](["123"]), 0)
        queries = [t for t, name in self.calls if name == "squeue"]
        early = [t for t in queries if t < 80]
        gaps = [b - a for a, b in zip(early, early[1:])]
        self.assertGreater(len(gaps), 2)
        self.assertGreater(gaps[-1], gaps[0])
        # File polling can delay noticing the new bytes by MAX_POLL seconds;
        # after that, the reset must query promptly instead of waiting a minute.
        next_query = next(t for t in queries if t > 80)
        self.assertLess(next_query, 80 + self.script["MAX_POLL"] + 5)
