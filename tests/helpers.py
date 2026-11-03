import io
import os
import runpy
import tempfile
import types
import unittest
from unittest.mock import patch


SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "slurmwatch")


class ScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.script = runpy.run_path(SCRIPT)
        self.temp = tempfile.TemporaryDirectory(prefix="slurmwatch-tests-")
        self.addCleanup(self.temp.cleanup)
        self.directory = self.temp.name
        self.output = io.BytesIO()
        self.errors = io.StringIO()
        self.stdout = types.SimpleNamespace(buffer=self.output)
        self.enter_patch(patch("sys.stdout", self.stdout))
        self.enter_patch(patch("sys.stderr", self.errors))

    def enter_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def write(self, name, data, mode="wb"):
        path = os.path.join(self.directory, name)
        with open(path, mode) as stream:
            stream.write(data)
        return path


class Clock:
    def __init__(self):
        self.now = 0.0
        self.events = []

    def monotonic(self):
        return self.now

    def at(self, when, callback):
        self.events.append((when, callback))
        self.events.sort(key=lambda event: event[0])

    def sleep(self, seconds):
        end = self.now + seconds
        if end > 180:
            raise AssertionError("watcher did not terminate within simulated deadline")
        while self.events and self.events[0][0] <= end:
            self.now, callback = self.events.pop(0)
            callback()
        self.now = end
