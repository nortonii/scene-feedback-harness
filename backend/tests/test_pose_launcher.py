"""Real CPU subprocesses verify pose workers cannot survive their server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "pose_launcher.py"

IDENTITY = r'''
def identity(pid):
    fields = Path("/proc").joinpath(str(pid), "stat").read_text().rpartition(") ")[2].split()
    return {"pid": pid, "start": fields[19], "group": int(fields[2])}
'''

GRANDCHILD = r'''
import json, os, signal, sys, time
from pathlib import Path
''' + IDENTITY + r'''
if sys.argv[2] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(json.dumps(identity(os.getpid())))
time.sleep(120)
'''

WORKER = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
''' + IDENTITY + r'''
root = Path(sys.argv[1])
if sys.argv[2] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable, str(root / "grandchild.py"), str(root / "grandchild.json"), sys.argv[3]])
deadline = time.monotonic() + 5
while not (root / "grandchild.json").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("grandchild fixture did not start")
    time.sleep(.01)
(root / "worker.json").write_text(json.dumps(identity(os.getpid())))
time.sleep(120)
'''

PARENT = r'''
import json, os, subprocess, sys, time
from pathlib import Path
''' + IDENTITY + r'''
root = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, sys.argv[2], str(os.getpid()), "--",
                          sys.executable, str(root / "worker.py"), str(root), sys.argv[3], sys.argv[4]],
                         start_new_session=True)
(root / "launcher.json").write_text(json.dumps(identity(child.pid)))
time.sleep(120)
'''


def process_state(record):
    """A missing PID, reused PID, or adopted zombie is no longer executing."""
    try:
        fields = (Path("/proc") / str(record["pid"]) / "stat").read_text().rpartition(") ")[2].split()
    except FileNotFoundError:
        return None
    if fields[19] != record["start"]:
        return None
    return fields[0]


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/stat").is_file(),
                     "parent death signals and process identities require Linux /proc")
class PoseLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pose-launcher-test-")
        self.root = Path(self.temporary.name)
        for name, source in (("parent", PARENT), ("worker", WORKER), ("grandchild", GRANDCHILD)):
            (self.root / (name + ".py")).write_text(source)
        self.processes = []

    def tearDown(self):
        # Only kill fixture PIDs whose /proc start time still matches our record.
        # Avoid broad process-name matching or signaling a potentially reused PID.
        for name in ("grandchild", "worker", "launcher"):
            path = self.root / (name + ".json")
            if path.is_file():
                try:
                    record = json.loads(path.read_text())
                    if process_state(record) not in (None, "Z", "X"):
                        os.kill(record["pid"], signal.SIGKILL)
                except (ProcessLookupError, ValueError):
                    pass
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.temporary.cleanup()

    def wait_for_records(self):
        deadline = time.monotonic() + 5
        names = ("launcher", "worker", "grandchild")
        while time.monotonic() < deadline:
            try:
                records = {name: json.loads((self.root / (name + ".json")).read_text()) for name in names}
                self.assertTrue(all(process_state(record) not in (None, "Z", "X") for record in records.values()))
                self.assertEqual(records["launcher"]["group"], records["launcher"]["pid"])
                self.assertEqual(records["worker"]["group"], records["worker"]["pid"])
                self.assertEqual(records["grandchild"]["group"], records["worker"]["pid"])
                self.assertNotEqual(records["launcher"]["group"], records["worker"]["group"])
                return records
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(.01)
        self.fail("CPU worker and grandchild fixtures did not become ready")

    def assert_stopped(self, records, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            states = {name: process_state(record) for name, record in records.items()}
            if all(state in (None, "Z", "X") for state in states.values()):
                return
            time.sleep(.02)
        self.fail(f"fixture processes still executing after shutdown: {states}")

    def start_tree(self, worker_term="default", grandchild_term="default"):
        parent = subprocess.Popen([sys.executable, str(self.root / "parent.py"), str(self.root),
                                   str(LAUNCHER), worker_term, grandchild_term],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(parent)
        return parent, self.wait_for_records()

    def test_parent_sigkill_stops_worker_and_term_ignoring_grandchild(self):
        parent, records = self.start_tree(grandchild_term="ignore")
        parent.kill()
        self.assertEqual(parent.wait(timeout=3), -signal.SIGKILL)
        self.assert_stopped(records)

    def test_launcher_sigterm_kills_group_even_when_worker_ignores_term(self):
        parent, records = self.start_tree(worker_term="ignore", grandchild_term="ignore")
        os.kill(records["launcher"]["pid"], signal.SIGTERM)
        self.assert_stopped(records)
        # Explicit cancellation of a worker must leave the server parent alive.
        self.assertIsNone(parent.poll())

    def test_sigterm_cancellation_stops_normal_worker_under_two_seconds(self):
        parent, records = self.start_tree(grandchild_term="ignore")
        started = time.monotonic()
        os.kill(records["launcher"]["pid"], signal.SIGTERM)
        self.assert_stopped(records, timeout=2)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(parent.poll())

    def test_exit_status_and_json_stdout_are_forwarded_without_supervisor_noise(self):
        lines = [{"type": "progress", "completed_frames": 0, "total_frames": 1},
                 {"type": "progress", "completed_frames": 1, "total_frames": 1}]
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                source = ("import json,sys\n"
                          "for item in " + repr(lines) + ": print(json.dumps(item),flush=True)\n"
                          "print('worker diagnostic',file=sys.stderr,flush=True)\n"
                          "sys.exit(" + str(exit_code) + ")\n")
                worker = self.root / "completed.py"
                worker.write_text(source)
                process = subprocess.Popen([sys.executable, str(LAUNCHER), str(os.getpid()), "--",
                                            sys.executable, str(worker)], start_new_session=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.processes.append(process)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, exit_code)
                self.assertEqual([json.loads(line) for line in stdout.splitlines()], lines)
                self.assertEqual(stderr, "worker diagnostic\n")

    def test_completed_runner_does_not_leave_background_helpers(self):
        worker = self.root / "completed.py"
        worker.write_text(r'''
import json, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
subprocess.Popen([sys.executable, str(root / "grandchild.py"), str(root / "grandchild.json"), "ignore"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 5
while not (root / "grandchild.json").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("background helper did not start")
    time.sleep(.01)
print(json.dumps({"type": "progress", "completed_frames": 1, "total_frames": 1}), flush=True)
''')
        process = subprocess.Popen([sys.executable, str(LAUNCHER), str(os.getpid()), "--",
                                    sys.executable, str(worker), str(self.root)], start_new_session=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout), {"type": "progress", "completed_frames": 1, "total_frames": 1})
        record = json.loads((self.root / "grandchild.json").read_text())
        self.assert_stopped({"grandchild": record}, timeout=2)


if __name__ == "__main__":
    unittest.main()
