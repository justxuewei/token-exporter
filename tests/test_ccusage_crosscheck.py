"""Cross-check token-exporter results against manual JSONL parsing.

For antcc directories, ccusage doesn't deduplicate (requestId is always
empty), so we verify our collector against a manual dedup-by-msg_id scan.
For Claude Code directories, we verify against ccusage CLI output.
"""

import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from watcher import CcusageCollector, _detect_agent

ANTCC_DATA_DIR = os.environ.get("ANTCC_DATA_DIR", os.path.expanduser("~/.codefuse/engine/cc"))


def _ccusage_available():
    try:
        result = subprocess.run(["ccusage", "--version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _antcc_data_available():
    projects_dir = os.path.join(ANTCC_DATA_DIR, "projects")
    return os.path.isdir(projects_dir)


skip_unless_integration = pytest.mark.skipif(
    not (_ccusage_available() and _antcc_data_available()),
    reason="ccusage not installed or ANTCC_DATA_DIR not found (set ANTCC_DATA_DIR to run)",
)


def _manual_deduped_totals(days_back: int = 7, timezone_name: str = "UTC") -> dict:
    """Manually scan JSONL files with msg_id dedup and return per-model totals."""
    tz = ZoneInfo(timezone_name)
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    projects_dir = os.path.join(ANTCC_DATA_DIR, "projects")
    totals = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0})

    for project_dir in Path(projects_dir).iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.rglob("*.jsonl"):
            seen = {}
            with open(jsonl_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    usage = obj.get("message", {}).get("usage")
                    if not usage:
                        continue
                    ts_str = obj.get("timestamp", "")
                    if not ts_str:
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        continue
                    local_ts = ts.astimezone(tz)
                    date_str = local_ts.strftime("%Y-%m-%d")
                    if date_str < since:
                        continue

                    msg_id = obj.get("message", {}).get("id", "")
                    model = obj.get("message", {}).get("model", "unknown") or "unknown"
                    inp = usage.get("input_tokens", 0) or 0
                    out = usage.get("output_tokens", 0) or 0
                    cache_c = usage.get("cache_creation_input_tokens", 0) or 0
                    cache_r = usage.get("cache_read_input_tokens", 0) or 0

                    if not inp and not out and not cache_c and not cache_r:
                        continue

                    if msg_id:
                        if msg_id in seen:
                            # Streaming update: replace with latest
                            old = seen[msg_id]
                            totals[model]["input_tokens"] -= old["input"]
                            totals[model]["output_tokens"] -= old["output"]
                            totals[model]["cache_creation_tokens"] -= old["cache_c"]
                            totals[model]["cache_read_tokens"] -= old["cache_r"]
                        seen[msg_id] = {"input": inp, "output": out, "cache_c": cache_c, "cache_r": cache_r}

                    totals[model]["input_tokens"] += inp
                    totals[model]["output_tokens"] += out
                    totals[model]["cache_creation_tokens"] += cache_c
                    totals[model]["cache_read_tokens"] += cache_r

    return dict(totals)


def _collector_model_totals(days_back: int, timezone_name: str = "UTC") -> dict:
    """Run CcusageCollector and return totals grouped by model."""
    totals = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    })

    def on_record(agent, record):
        model = record["model"]
        t = totals[model]
        t["input_tokens"] += record["input_tokens"]
        t["output_tokens"] += record["output_tokens"]
        t["cache_creation_tokens"] += record["cache_creation_tokens"]
        t["cache_read_tokens"] += record["cache_read_tokens"]

    collector = CcusageCollector(
        claude_dirs=[ANTCC_DATA_DIR],
        days_back=days_back,
        on_record=on_record,
        timezone_name=timezone_name,
    )
    collector.scan_history()
    return dict(totals)


ZERO_TOTALS = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}


def _assert_totals_match(expected: dict, actual: dict, label: str = ""):
    """Assert that expected and actual totals match for all models."""
    all_models = set(expected.keys()) | set(actual.keys())
    if not all_models:
        pytest.skip("No usage data found")

    for model in sorted(all_models):
        exp = expected.get(model, ZERO_TOTALS)
        act = actual.get(model, ZERO_TOTALS)
        for field in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"):
            assert exp[field] == act[field], (
                f"{label}Model {model!r} {field} mismatch: expected={exp[field]} actual={act[field]}"
            )


@skip_unless_integration
class TestAntccCrosscheck:
    """Verify CcusageCollector matches manual deduped JSONL scan for antcc data."""

    def test_totals_match_last_7_days(self):
        """Last 7 days totals should match manual deduped scan."""
        expected = _manual_deduped_totals(days_back=7)
        actual = _collector_model_totals(days_back=7)

        if not expected:
            pytest.skip("No antcc data for last 7 days")

        _assert_totals_match(expected, actual, label="7-day: ")