import os
import subprocess
from unittest.mock import patch

from helpers import ScriptTestCase


class OutputPathTests(ScriptTestCase):
    def test_slurm_response_with_spaces_and_relative_pattern(self):
        response = (
            "JobId=123 JobName=my job UserId=alice(1000) JobState=RUNNING "
            "WorkDir=%s StdOut=logs/%%x-%%j-%%u-%%%%.out\n" % self.directory
        )
        with patch("shutil.which", return_value="/mock/slurm"), patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, response, ""),
        ):
            candidates, rc, state = self.script["resolve"]("123")
        self.assertEqual(rc, 0)
        self.assertEqual(state, "RUNNING")
        self.assertEqual(candidates, [
            os.path.join(self.directory, "logs/my job-123-alice-%.out")
        ])

    def test_absolute_path_does_not_use_workdir(self):
        self.assertEqual(self.script["output_path"]({
            "StdOut": "/logs/../logs/job-%j.out", "JobId": "123",
            "WorkDir": self.directory,
        }), "/logs/job-123.out")

    def test_unresolvable_patterns(self):
        for pattern in ("log-%N", "log-%s", "log-%J"):
            with self.subTest(pattern=pattern):
                self.assertIsNone(self.script["output_path"]({"StdOut": pattern}))

    def test_missing_and_disabled_output(self):
        self.assertIsNone(self.script["output_path"]({}))
        for fields in ({"StdOut": ""}, {"StdOut": "NONE"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.script["output_path"](fields), os.devnull)
        self.assertEqual(self.script["output_path"]({"StdOut": os.devnull}), os.devnull)

    def test_explicit_path_with_spaces(self):
        path = os.path.join(self.directory, "my job.out")
        self.assertEqual(self.script["resolve"](path), ([path], 0, ""))

    def test_directory_is_rejected(self):
        self.assertEqual(self.script["resolve"](self.directory), (None, 2, ""))

    def test_inaccessible_workdir_is_not_replaced_by_current_directory(self):
        workdir = os.path.join(self.directory, "unmounted")
        self.assertEqual(self.script["output_path"]({
            "JobId": "123", "WorkDir": workdir, "StdOut": "logs/%j.out",
        }), os.path.join(workdir, "logs/123.out"))

    def test_numeric_padding_matches_slurm(self):
        fields = {"JobId": "501", "ArrayJobId": "500", "ArrayTaskId": "7"}
        self.assertEqual(self.script["expand_escapes"](
            "%4A_%04a-%6j-%%.out", fields
        ), "0500_0007-000501-%.out")
        self.assertEqual(self.script["expand_escapes"]("%20a", fields), "0000000007")
        self.assertEqual(self.script["expand_escapes"]("%2j", fields), "501")

    def test_array_pattern_uses_separate_slurm_array_fields(self):
        self.assertEqual(self.script["output_path"]({
            "JobId": "501", "ArrayJobId": "500", "ArrayTaskId": "7",
            "StdOut": "/logs/slurm-%A_%a.out",
        }), "/logs/slurm-500_7.out")

    def test_array_job_token_is_individual_numeric_id(self):
        self.assertEqual(self.script["expand_escapes"]("%j-%A-%a", {
            "JobId": "501", "ArrayJobId": "500", "ArrayTaskId": "7",
        }), "501-500-7")

    def test_array_range_is_not_a_single_output_file(self):
        self.assertIsNone(self.script["expand_escapes"]("%A_%a", {
            "JobId": "500", "ArrayJobId": "500", "ArrayTaskId": "1-10",
        }))

    def test_non_array_tokens_match_slurm_defaults(self):
        self.assertEqual(self.script["expand_escapes"]("%j-%A-%a", {
            "JobId": "123",
        }), "123-123-4294967294")

    def test_task_rank_is_not_array_index(self):
        self.assertIsNone(self.script["expand_escapes"]("%t", {
            "JobId": "501", "ArrayJobId": "500", "ArrayTaskId": "7",
        }))
