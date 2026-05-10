import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from watcher import CcusageCollector, CodexCollector, _extract_project


SAMPLE_CCUSAGE_INSTANCES = {
    "projects": {
        "-home-nxw-developer-token-exporter": [
            {
                "date": "2026-05-09",
                "inputTokens": 1000,
                "outputTokens": 200,
                "cacheCreationTokens": 50,
                "cacheReadTokens": 500,
                "totalTokens": 1750,
                "totalCost": 0.01,
                "modelsUsed": ["GLM-5.1"],
                "modelBreakdowns": [
                    {
                        "modelName": "GLM-5.1",
                        "inputTokens": 1000,
                        "outputTokens": 200,
                        "cacheCreationTokens": 50,
                        "cacheReadTokens": 500,
                        "cost": 0.01,
                    }
                ],
            }
        ],
        "-home-nxw-developer-devkit-ant": [
            {
                "date": "2026-05-09",
                "inputTokens": 500,
                "outputTokens": 100,
                "cacheCreationTokens": 0,
                "cacheReadTokens": 300,
                "totalTokens": 900,
                "totalCost": 0,
                "modelsUsed": ["GLM-5.1"],
                "modelBreakdowns": [
                    {
                        "modelName": "GLM-5.1",
                        "inputTokens": 500,
                        "outputTokens": 100,
                        "cacheCreationTokens": 0,
                        "cacheReadTokens": 300,
                        "cost": 0,
                    }
                ],
            }
        ],
    }
}

SAMPLE_CODEX_DAILY = {
    "daily": [
        {
            "date": "2026-05-09",
            "inputTokens": 800,
            "outputTokens": 150,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 400,
            "totalTokens": 1350,
            "totalCost": 0,
            "modelsUsed": ["codex-1"],
            "modelBreakdowns": [
                {
                    "modelName": "codex-1",
                    "inputTokens": 800,
                    "outputTokens": 150,
                    "cacheCreationTokens": 0,
                    "cacheReadTokens": 400,
                    "cost": 0,
                }
            ],
        }
    ],
    "totals": {
        "inputTokens": 800,
        "outputTokens": 150,
        "cacheCreationTokens": 0,
        "cacheReadTokens": 400,
        "totalTokens": 1350,
        "totalCost": 0,
    },
}


SAMPLE_CODEX_DAILY_MODELS = {
    "daily": [
        {
            "date": "May 10, 2026",
            "inputTokens": 1000,
            "cachedInputTokens": 400,
            "outputTokens": 150,
            "reasoningOutputTokens": 25,
            "totalTokens": 1150,
            "costUSD": 0.42,
            "models": {
                "gpt-5.5": {
                    "inputTokens": 1000,
                    "cachedInputTokens": 400,
                    "outputTokens": 150,
                    "reasoningOutputTokens": 25,
                    "totalTokens": 1150,
                    "isFallback": False,
                }
            },
        }
    ],
    "totals": {
        "inputTokens": 1000,
        "cachedInputTokens": 400,
        "outputTokens": 150,
        "reasoningOutputTokens": 25,
        "totalTokens": 1150,
        "costUSD": 0.42,
    },
}


class TestExtractProject(unittest.TestCase):
    def test_standard_path(self):
        assert _extract_project("-home-nxw-developer-token-exporter") == "token-exporter"

    def test_nested_path(self):
        assert _extract_project("-home-nxw-developer-devkit-ant") == "devkit-ant"

    def test_short_path(self):
        assert _extract_project("-home-user") == "home-user"

    def test_strips_leading_dashes(self):
        assert _extract_project("--home-nxw-developer-my-project") == "my-project"


class TestCcusageCollector(unittest.TestCase):
    @patch("watcher.os.path.isdir", return_value=True)
    @patch("watcher._run_ccusage")
    def test_scan_history_emits_records(self, mock_run, mock_isdir):
        mock_run.return_value = SAMPLE_CCUSAGE_INSTANCES
        records = []
        collector = CcusageCollector(
            claude_dirs=["/fake/dir"],
            days_back=7,
            on_record=lambda agent, rec: records.append((agent, rec)),
        )
        collector.scan_history()

        assert len(records) == 2
        agent0, rec0 = records[0]
        assert agent0 == "unknown"
        assert rec0["model"] == "GLM-5.1"
        assert rec0["input_tokens"] == 1000
        assert rec0["output_tokens"] == 200
        assert rec0["cache_creation_tokens"] == 50
        assert rec0["cache_read_tokens"] == 500
        assert rec0["cost_usd"] == 0.01

    @patch("watcher.os.path.isdir", return_value=True)
    @patch("watcher._run_ccusage")
    def test_delta_tracking_no_double_count(self, mock_run, mock_isdir):
        mock_run.return_value = SAMPLE_CCUSAGE_INSTANCES
        records = []
        collector = CcusageCollector(
            claude_dirs=["/fake/dir"],
            days_back=7,
            on_record=lambda agent, rec: records.append((agent, rec)),
        )
        # First scan: all data is new
        collector.scan_history()
        assert len(records) == 2

        # Second scan: same data, no deltas → no new records
        collector.scan_history()
        assert len(records) == 2

    @patch("watcher.os.path.isdir", return_value=True)
    @patch("watcher._run_ccusage")
    def test_delta_tracking_incremental(self, mock_run, mock_isdir):
        records = []
        collector = CcusageCollector(
            claude_dirs=["/fake/dir"],
            days_back=7,
            on_record=lambda agent, rec: records.append((agent, rec)),
        )

        # First scan
        mock_run.return_value = SAMPLE_CCUSAGE_INSTANCES
        collector.scan_history()
        assert len(records) == 2
        assert records[0][1]["input_tokens"] == 1000

        # Second scan with increased tokens
        updated = json.loads(json.dumps(SAMPLE_CCUSAGE_INSTANCES))
        updated["projects"]["-home-nxw-developer-token-exporter"][0]["inputTokens"] = 1500
        updated["projects"]["-home-nxw-developer-token-exporter"][0]["modelBreakdowns"][0]["inputTokens"] = 1500
        mock_run.return_value = updated
        collector.scan_history()
        assert len(records) == 3
        assert records[2][1]["input_tokens"] == 500  # 1500 - 1000 delta

    @patch("watcher.os.path.isdir", return_value=True)
    @patch("watcher._run_ccusage")
    def test_new_day_produces_new_records(self, mock_run, mock_isdir):
        records = []
        collector = CcusageCollector(
            claude_dirs=["/fake/dir"],
            days_back=7,
            on_record=lambda agent, rec: records.append((agent, rec)),
        )

        mock_run.return_value = SAMPLE_CCUSAGE_INSTANCES
        collector.scan_history()
        assert len(records) == 2

        # Add a new day's data
        updated = json.loads(json.dumps(SAMPLE_CCUSAGE_INSTANCES))
        updated["projects"]["-home-nxw-developer-token-exporter"].append({
            "date": "2026-05-10",
            "inputTokens": 300,
            "outputTokens": 50,
            "cacheCreationTokens": 0,
            "cacheReadTokens": 100,
            "totalTokens": 450,
            "totalCost": 0,
            "modelsUsed": ["GLM-5.1"],
            "modelBreakdowns": [
                {
                    "modelName": "GLM-5.1",
                    "inputTokens": 300,
                    "outputTokens": 50,
                    "cacheCreationTokens": 0,
                    "cacheReadTokens": 100,
                    "cost": 0,
                }
            ],
        })
        mock_run.return_value = updated
        collector.scan_history()
        assert len(records) == 3
        assert records[2][1]["input_tokens"] == 300

    @patch("watcher._run_ccusage")
    def test_skips_empty_dirs(self, mock_run):
        records = []
        collector = CcusageCollector(
            claude_dirs=["/nonexistent"],
            days_back=7,
            on_record=lambda agent, rec: records.append((agent, rec)),
        )
        collector.scan_history()
        assert len(records) == 0
        mock_run.assert_not_called()

    @patch("watcher._run_ccusage")
    def test_ccusage_failure_is_handled(self, mock_run):
        mock_run.return_value = None
        records = []
        collector = CcusageCollector(
            claude_dirs=["/fake/dir"],
            days_back=7,
            on_record=lambda agent, rec: records.append((agent, rec)),
        )
        collector.scan_history()
        assert len(records) == 0


class TestCodexCollector(unittest.TestCase):
    def test_antcodex_parser_emits_project_records_and_skips_duplicate_token_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / "codex"
            session_dir = codex_dir / "sessions" / "2026" / "05" / "10"
            session_dir.mkdir(parents=True)
            session_file = session_dir / "rollout-test.jsonl"
            rows = [
                {
                    "timestamp": "2026-05-10T10:00:00Z",
                    "type": "session_meta",
                    "payload": {"cwd": "/home/user/developer/token-exporter"},
                },
                {
                    "timestamp": "2026-05-10T10:00:01Z",
                    "type": "turn_context",
                    "payload": {"cwd": "/home/user/developer/token-exporter", "model": "gpt-5.5"},
                },
                {
                    "timestamp": "2026-05-10T10:00:02Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 400,
                                "output_tokens": 50,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-05-10T10:00:03Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1000,
                                "cached_input_tokens": 400,
                                "output_tokens": 50,
                            }
                        },
                    },
                },
                {
                    "timestamp": "2026-05-10T10:00:04Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 1500,
                                "cached_input_tokens": 700,
                                "output_tokens": 80,
                            }
                        },
                    },
                },
            ]
            session_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

            records = []
            collector = CodexCollector(
                codex_dirs=[str(codex_dir)],
                days_back=7,
                on_record=lambda agent, rec: records.append((agent, rec)),
                timezone_name="Asia/Shanghai",
            )
            collector.scan_history()

        assert len(records) == 1
        agent, rec = records[0]
        assert agent == "antcodex"
        assert rec["project"] == "token-exporter"
        assert rec["model"] == "gpt-5.5"
        assert rec["input_tokens"] == 800
        assert rec["cache_read_tokens"] == 700
        assert rec["output_tokens"] == 80
        assert rec["cost_usd"] == 0

    def test_antcodex_parser_applies_pricing(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / "codex"
            session_dir = codex_dir / "sessions" / "2026" / "05" / "10"
            session_dir.mkdir(parents=True)
            session_file = session_dir / "rollout.jsonl"
            rows = [
                {"timestamp": "2026-05-10T10:00:00Z", "type": "session_meta",
                 "payload": {"cwd": "/home/user/developer/proj"}},
                {"timestamp": "2026-05-10T10:00:01Z", "type": "turn_context",
                 "payload": {"cwd": "/home/user/developer/proj", "model": "gpt-5"}},
                {"timestamp": "2026-05-10T10:00:02Z", "type": "event_msg",
                 "payload": {"type": "token_count", "info": {"total_token_usage": {
                     "input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 100_000}}}},
            ]
            session_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

            records = []
            rates = {"gpt-5": {"input": 1.25, "cached": 0.125, "output": 10.0, "cache_creation": 1.25}}
            collector = CodexCollector(
                codex_dirs=[str(codex_dir)],
                days_back=7,
                on_record=lambda agent, rec: records.append((agent, rec)),
                pricing_rates=rates,
            )
            collector.scan_history()

        assert len(records) == 1
        _, rec = records[0]
        # 1M input @ $1.25/MTok + 100K output @ $10/MTok = 1.25 + 1.0 = 2.25
        assert rec["cost_usd"] == pytest.approx(2.25)


if __name__ == "__main__":
    unittest.main()
