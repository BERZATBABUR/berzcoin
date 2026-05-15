"""Safety tests for reset_datadir_safe.sh."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "reset_datadir_safe.sh"


class TestResetScriptSafety(unittest.TestCase):
    def test_regtest_reset_succeeds_with_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp) / "reg"
            datadir.mkdir(parents=True, exist_ok=True)
            (datadir / ".network").write_text("regtest\n", encoding="utf-8")
            (datadir / "foo.txt").write_text("x", encoding="utf-8")
            cp = subprocess.run(
                [str(SCRIPT), "--network", "regtest", "--datadir", str(datadir), "--confirm-reset"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertTrue((datadir / ".network").exists())
            self.assertFalse((datadir / "foo.txt").exists())

    def test_mainnet_reset_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp) / "main"
            datadir.mkdir(parents=True, exist_ok=True)
            (datadir / ".network").write_text("mainnet\n", encoding="utf-8")
            cp = subprocess.run(
                [str(SCRIPT), "--network", "mainnet", "--datadir", str(datadir), "--confirm-reset"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("only allowed for regtest/dev", cp.stderr.lower())

    def test_missing_confirm_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            datadir = Path(tmp) / "reg"
            datadir.mkdir(parents=True, exist_ok=True)
            (datadir / ".network").write_text("regtest\n", encoding="utf-8")
            cp = subprocess.run(
                [str(SCRIPT), "--network", "regtest", "--datadir", str(datadir)],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("requires --confirm-reset", cp.stderr.lower())


if __name__ == "__main__":
    unittest.main()

