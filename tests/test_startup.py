import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.stress


class ConcurrentStartupTests(unittest.TestCase):
    def test_twenty_concurrent_hooks_never_emit_a_traceback(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            home = root / "home"
            data_home = root / "data"
            home.mkdir()
            data_home.mkdir()
            environment = os.environ.copy()
            environment.update({"HOME": str(home), "XDG_DATA_HOME": str(data_home)})

            hooks = [
                subprocess.Popen(
                    [sys.executable, "-m", "pokedex_cli", "hook"],
                    cwd=Path(__file__).resolve().parent.parent,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(20)
            ]
            try:
                outputs = [hook.communicate(timeout=20) for hook in hooks]
            finally:
                for hook in hooks:
                    if hook.poll() is None:
                        hook.terminate()
                for hook in hooks:
                    if hook.poll() is None:
                        hook.wait(timeout=5)

            self.assertTrue(all(hook.returncode == 0 for hook in hooks))
            self.assertTrue(all("Traceback" not in stderr for _, stderr in outputs))
            data_dir = data_home / "pokedex-cli"
            connection = sqlite3.connect(data_dir / "pokedex.db")
            try:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM activity_state").fetchone()[0],
                    1,
                )
            finally:
                connection.close()
            self.assertFalse((data_dir / "inventory.json").exists())
