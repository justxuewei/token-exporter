import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from watcher import JSONLWatcher, _detect_agent, find_jsonl_files


class TestDetectAgent:
    def test_claude_code(self):
        assert _detect_agent("/home/user/.claude") == "cc"
        assert _detect_agent("/Users/me/.claude") == "cc"

    def test_antcc(self):
        assert _detect_agent("/home/user/.codefuse/engine/cc") == "antcc"

    def test_unknown(self):
        assert _detect_agent("/home/user/.codefuse/codefuse-cc") == "unknown"

    def test_antcc_codex(self):
        assert _detect_agent("/home/user/.codefuse/engine/codex") == "antcodex"

    def test_codex(self):
        assert _detect_agent("/home/user/.codex") == "codex"
        assert _detect_agent("/Users/me/.codex") == "codex"


class TestAntCodexSessions:
    def test_find_jsonl_files_preserves_antcodex_agent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = os.path.join(tmpdir, ".codefuse", "engine", "codex")
            sessions_dir = os.path.join(base_dir, "sessions", "2026", "04", "30")
            os.makedirs(sessions_dir)
            filepath = os.path.join(sessions_dir, "session.jsonl")
            open(filepath, "w").close()

            assert find_jsonl_files([base_dir]) == {filepath: ("antcodex", "unknown")}

    def test_antcodex_session_records_use_antcodex_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = os.path.join(tmpdir, ".codefuse", "engine", "codex")
            sessions_dir = os.path.join(base_dir, "sessions", "2026", "04", "30")
            os.makedirs(sessions_dir)
            filepath = os.path.join(sessions_dir, "session.jsonl")
            with open(filepath, "w") as f:
                f.write(json.dumps({
                    "type": "turn_context",
                    "payload": {
                        "model": "gpt-5.4",
                        "cwd": "/home/nxw/developer/token-exporter",
                    },
                }) + "\n")
                f.write(json.dumps({
                    "type": "event_msg",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "cached_input_tokens": 10,
                                "reasoning_output_tokens": 5,
                            },
                            "last_token_usage": {
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "cached_input_tokens": 10,
                                "reasoning_output_tokens": 5,
                            },
                        },
                    },
                }) + "\n")

            records = []

            def on_record(agent, record):
                records.append((agent, record))

            watcher = JSONLWatcher(
                claude_dirs=[base_dir],
                days_back=7,
                state_file="",
                on_record=on_record,
            )
            watcher.scan_history()

            assert len(records) == 1
            assert records[0][0] == "antcodex"
            assert records[0][1]["project"] == "token-exporter"
