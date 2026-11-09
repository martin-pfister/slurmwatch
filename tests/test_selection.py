import subprocess
from unittest.mock import patch

from helpers import Clock, ScriptTestCase


class SelectionTests(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.path = self.write("job.out", b"job output\n")
        self.queue = "123\n"
        self.calls = []
        self.enter_patch(patch("time.monotonic", self.clock.monotonic))
        self.enter_patch(patch("time.sleep", self.clock.sleep))
        self.enter_patch(patch("signal.signal"))
        self.enter_patch(patch("shutil.which", return_value="/mock/slurm"))
        self.enter_patch(patch("subprocess.run", side_effect=self.command))

    def command(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "squeue":
            self.assertEqual(cmd, ["squeue", "--me", "--noheader", "--array", "--format=%i"])
            out = self.queue
        elif cmd[0] == "scontrol":
            out = "JobId=123 JobState=COMPLETED StdOut=%s\n" % self.path
        else:
            raise AssertionError("unexpected command: %r" % cmd)
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def test_only_job_is_resolved_and_followed(self):
        self.assertEqual(self.script["main"]([]), 0)
        self.assertEqual(self.output.getvalue(), b"job output\n")
        self.assertEqual(self.calls[1], ["scontrol", "-o", "show", "job", "123"])
        self.assertEqual(len(self.calls), 2)

    def test_only_array_task_is_selected(self):
        self.queue = " 500_7 \n\n"
        self.assertEqual(self.script["main"]([]), 0)
        self.assertEqual(self.calls[1][-1], "500_7")

    def test_empty_queue(self):
        self.queue = "\n  \n"
        self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("no jobs found in your queue", self.errors.getvalue())
        self.assertIn("slurmwatch TARGET", self.errors.getvalue())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.output.getvalue(), b"")

    def test_multiple_jobs(self):
        self.queue = "123\n456\n"
        self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("123, 456", self.errors.getvalue())
        self.assertIn("slurmwatch JOB_ID", self.errors.getvalue())
        self.assertEqual(len(self.calls), 1)

    def test_multiple_array_tasks_are_ambiguous(self):
        self.queue = "500_7\n500_8\n"
        self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("multiple jobs", self.errors.getvalue())
        self.assertEqual(len(self.calls), 1)

    def test_unsupported_id_is_not_treated_as_a_path(self):
        self.queue = "500_[1-8]\n"
        self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("unsupported job ID", self.errors.getvalue())
        self.assertEqual(len(self.calls), 1)

    def test_missing_squeue(self):
        with patch("shutil.which", return_value=None):
            self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("squeue not found", self.errors.getvalue())
        self.assertNotIn("no jobs found", self.errors.getvalue())

    def test_failed_squeue(self):
        result = subprocess.CompletedProcess([], 1, "", "controller unavailable")
        with patch("subprocess.run", return_value=result):
            self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("cannot query your queue", self.errors.getvalue())
        self.assertNotIn("no jobs found", self.errors.getvalue())

    def test_squeue_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("squeue", 10)):
            self.assertEqual(self.script["main"]([]), 2)
        self.assertIn("cannot query your queue", self.errors.getvalue())

    def test_explicit_job_bypasses_selection(self):
        self.assertEqual(self.script["main"](["123"]), 0)
        self.assertEqual([cmd[0] for cmd in self.calls], ["scontrol"])

    def test_explicit_path_bypasses_slurm(self):
        with patch("time.sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(self.script["main"]([self.path]), 130)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.output.getvalue(), b"job output\n")
