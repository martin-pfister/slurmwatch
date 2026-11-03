import os
import runpy
import subprocess
import unittest
from unittest.mock import patch


SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "slurmwatch")
job_state = runpy.run_path(SCRIPT)["job_state"]


class JobStateTests(unittest.TestCase):
    def query(self, responses):
        with patch("shutil.which", return_value="/usr/bin/slurm"), patch(
            "subprocess.run", side_effect=responses
        ) as run:
            state = job_state("123")
        return state, run

    def test_pruned_job_uses_accounting(self):
        state, run = self.query([
            subprocess.CompletedProcess([], 1, "", "slurm_load_jobs error: Invalid job id specified\n"),
            subprocess.CompletedProcess([], 0, "COMPLETED|\n", ""),
        ])
        self.assertEqual(state, "COMPLETED")
        self.assertEqual(run.call_args_list[1][0][0][0], "sacct")
        self.assertEqual(run.call_args_list[0][1]["env"]["LC_ALL"], "C")

    def test_unknown_job_is_gone(self):
        state, _ = self.query([
            subprocess.CompletedProcess([], 1, "", "Invalid job id specified"),
            subprocess.CompletedProcess([], 0, "", ""),
        ])
        self.assertEqual(state, "")

    def test_controller_failure_remains_unknown(self):
        state, run = self.query([
            subprocess.CompletedProcess([], 1, "", "Unable to contact slurm controller"),
        ])
        self.assertIsNone(state)
        self.assertEqual(run.call_count, 1)

    def test_timeout_remains_unknown(self):
        state, run = self.query([subprocess.TimeoutExpired("squeue", 10)])
        self.assertIsNone(state)
        self.assertEqual(run.call_count, 1)

    def test_empty_queue_uses_accounting(self):
        state, _ = self.query([
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "FAILED|\n", ""),
        ])
        self.assertEqual(state, "FAILED")

    def test_active_job_needs_no_accounting(self):
        state, run = self.query([
            subprocess.CompletedProcess([], 0, "RUNNING\n", ""),
        ])
        self.assertEqual(state, "RUNNING")
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
