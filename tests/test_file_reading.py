import os

from helpers import ScriptTestCase


class FileReadingTests(ScriptTestCase):
    def test_reads_binary_content_across_chunks_then_only_appended_bytes(self):
        data = b"\x00\xff\r\n" * self.script["CHUNK"] + b"last line"
        path = self.write("job.out", data)
        pos = self.script["pump"](path, 0)
        self.assertEqual(pos, len(data))
        self.assertEqual(self.output.getvalue(), data)
        self.assertEqual(self.script["pump"](path, pos), pos)
        self.assertEqual(self.output.getvalue(), data)
        self.write("job.out", b" appended\n", "ab")
        pos = self.script["pump"](path, pos)
        self.assertEqual(pos, len(data + b" appended\n"))
        self.assertEqual(self.output.getvalue(), data + b" appended\n")

    def test_truncation_restarts_at_beginning(self):
        path = self.write("job.out", b"old long content\n")
        pos = self.script["pump"](path, 0)
        self.write("job.out", b"new\n")
        self.assertEqual(self.script["pump"](path, pos), 4)
        self.assertEqual(self.output.getvalue(), b"old long content\nnew\n")
        self.assertIn("file shrank", self.errors.getvalue())

    def test_missing_file_can_be_retried(self):
        path = os.path.join(self.directory, "later.out")
        self.assertEqual(self.script["pump"](path, 0), 0)
        self.write("later.out", b"ready\n")
        self.assertEqual(self.script["pump"](path, 0), 6)
        self.assertEqual(self.output.getvalue(), b"ready\n")

    def test_glob_chooses_first_regular_file(self):
        os.mkdir(os.path.join(self.directory, "a.out"))
        self.write("c.out", b"c")
        first = self.write("b.out", b"b")
        pattern = os.path.join(self.directory, "*.out")
        self.assertEqual(self.script["locate"]([pattern]), first)

    def test_waiting_glob_discovers_new_file(self):
        pattern = os.path.join(self.directory, "*.out")
        self.assertIsNone(self.script["locate"]([pattern]))
        path = self.write("job.out", b"hello")
        self.assertEqual(self.script["locate"]([pattern]), path)

    def test_literal_filename_takes_priority_over_glob_match(self):
        path = self.write("run[1].out", b"correct")
        self.write("run1.out", b"wrong")
        self.assertEqual(self.script["locate"]([path]), path)

    def test_literal_slurm_path_does_not_match_another_file(self):
        path = os.path.join(self.directory, "run[1].out")
        self.write("run1.out", b"wrong")
        self.assertIsNone(self.script["locate"]([path], allow_glob=False))
        self.write("run[1].out", b"correct")
        self.assertEqual(self.script["locate"]([path], allow_glob=False), path)
