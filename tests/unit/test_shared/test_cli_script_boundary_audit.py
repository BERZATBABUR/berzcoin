"""Tests for CLI/script boundary audit helper."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "audit_cli_script_boundaries.py"


class TestCLIScriptBoundaryAudit(unittest.TestCase):
    def test_audit_runs_and_passes(self) -> None:
        cp = subprocess.run(["python3", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("passed", (cp.stdout + cp.stderr).lower())


if __name__ == "__main__":
    unittest.main()

