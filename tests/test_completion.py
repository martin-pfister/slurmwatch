import os
import subprocess
import unittest
from unittest.mock import patch

from helpers import Clock, ScriptTestCase


class CompletionTests(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.path = os.path.join(self.directory, "job.out")
        self.completed_at = 8
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
            out = "JobId=123 JobState=RUNNING StdOut=%s\n" % self.path
        elif name == "squeue":
            if self.clock.now < self.unavailable_until:
                return subprocess.CompletedProcess(cmd, 1, "", "Unable to contact slurm controller")
            if self.clock.now >= self.completed_at:
                return subprocess.CompletedProcess(cmd, 1, "", "Invalid job id specified")
            out = "RUNNING\n"
        elif name == "sacct":
            out = "COMPLETED|\n"
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

    @unittest.expectedFailure
    def test_file_appearing_just_after_completion_is_drained(self):
        # Known bug: the waiting-for-file branch skips the drain period.
        self.completed_at = 0
        self.clock.at(5, lambda: self.write("job.out", b"delayed visibility\n"))
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual(self.output.getvalue(), b"delayed visibility\n")
