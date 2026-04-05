"""Helpers for writing chaos/fuzz test artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable


def get_artifact_dir() -> Path:
    path = os.getenv("BERZ_TEST_ARTIFACT_DIR", "").strip()
    if path:
        out = Path(path)
    else:
        out = Path(tempfile.gettempdir()) / "berzcoin-test-artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_json_artifact(name: str, payload: Dict[str, Any]) -> Path:
    out = get_artifact_dir() / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def write_jsonl_artifact(name: str, rows: Iterable[Dict[str, Any]]) -> Path:
    out = get_artifact_dir() / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return out
