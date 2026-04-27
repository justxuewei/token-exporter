import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("token-stats")

PROJECTS_DIR = "projects"
GLOB_PATTERN = "**/*.jsonl"


def find_jsonl_files(claude_dirs: list[str]) -> dict[str, str]:
    """Find all JSONL files and return {filepath: agent_name}."""
    files = {}
    for base_dir in claude_dirs:
        projects_dir = os.path.join(base_dir, PROJECTS_DIR)
        if not os.path.isdir(projects_dir):
            continue
        agent = _detect_agent(base_dir)
        for p in Path(projects_dir).rglob("*.jsonl"):
            files[str(p)] = agent
    return files


def _detect_agent(base_dir: str) -> str:
    base = os.path.basename(os.path.normpath(base_dir))
    if "codex" in base.lower():
        return "codex"
    if "codefuse" in base.lower() or "cc" in base.lower():
        return "antcc"
    return "claude-code"


def parse_line(line: str) -> dict | None:
    """Parse a single JSONL line into a usage record."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    usage = obj.get("message", {}).get("usage")
    if not usage:
        return None

    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    if not input_tokens and not output_tokens:
        return None

    msg_id = obj.get("message", {}).get("id", "")
    request_id = obj.get("requestId", "")
    dedup_key = f"{msg_id}:{request_id}" if msg_id or request_id else None

    ts_str = obj.get("timestamp", "")
    timestamp = None
    if ts_str:
        try:
            timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return {
        "timestamp": timestamp,
        "model": obj.get("message", {}).get("model", "unknown") or "unknown",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "cost_usd": obj.get("costUSD", 0) or 0,
        "dedup_key": dedup_key,
    }


class JSONLWatcher:
    def __init__(self, claude_dirs: list[str], days_back: int = 7, on_record=None):
        self.claude_dirs = claude_dirs
        self.days_back = days_back
        self.on_record = on_record
        self._file_positions: dict[str, int] = {}
        self._seen_keys: set[str] = set()

    def scan_history(self):
        """Read existing JSONL files to populate historical data."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        files = find_jsonl_files(self.claude_dirs)
        logger.info("Scanning %d JSONL files for history...", len(files))
        count = 0
        for filepath, agent in files.items():
            pos, n = self._read_file(filepath, agent, cutoff)
            self._file_positions[filepath] = pos
            count += n
        logger.info("Scanned %d historical records", count)

    def check_updates(self):
        """Check for new lines in known files and any new files."""
        files = find_jsonl_files(self.claude_dirs)

        for filepath, agent in files.items():
            if filepath not in self._file_positions:
                self._file_positions[filepath] = 0
            pos, _ = self._read_file(filepath, agent, None)
            self._file_positions[filepath] = pos

        # Clean up deleted files
        gone = set(self._file_positions.keys()) - set(files.keys())
        for f in gone:
            del self._file_positions[f]

    def _read_file(self, filepath: str, agent: str, cutoff: datetime | None) -> tuple[int, int]:
        """Read new lines from a file starting from tracked position.
        Returns (new_position, record_count)."""
        count = 0
        try:
            size = os.path.getsize(filepath)
        except OSError:
            return self._file_positions.get(filepath, 0), 0

        start_pos = self._file_positions.get(filepath, 0)
        if start_pos > size:
            start_pos = 0

        if start_pos == size:
            return size, 0

        try:
            with open(filepath, "r") as f:
                f.seek(start_pos)
                for line in f:
                    record = parse_line(line)
                    if record is None:
                        continue
                    if cutoff and record["timestamp"] and record["timestamp"] < cutoff:
                        continue
                    if record["dedup_key"]:
                        if record["dedup_key"] in self._seen_keys:
                            continue
                        self._seen_keys.add(record["dedup_key"])

                    if self.on_record:
                        self.on_record(agent, record)
                    count += 1
                return f.tell(), count
        except OSError as e:
            logger.error("Error reading %s: %s", filepath, e)
            return start_pos, 0