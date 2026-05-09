import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("token-stats")

PROJECTS_DIR = "projects"
SESSIONS_DIR = "sessions"
GLOB_PATTERN = "**/*.jsonl"


def find_jsonl_files(claude_dirs: list[str]) -> dict[str, tuple[str, str]]:
    """Find all JSONL files and return {filepath: (agent_name, project)}."""
    files = {}
    for base_dir in claude_dirs:
        agent = _detect_agent(base_dir)
        # Claude Code / AntCC: projects/ directory
        projects_dir = os.path.join(base_dir, PROJECTS_DIR)
        if os.path.isdir(projects_dir):
            for p in Path(projects_dir).rglob("*.jsonl"):
                project = _extract_project(str(p), projects_dir)
                files[str(p)] = (agent, project)
        # Codex: sessions/ directory
        sessions_dir = os.path.join(base_dir, SESSIONS_DIR)
        if os.path.isdir(sessions_dir):
            for p in Path(sessions_dir).rglob("*.jsonl"):
                files[str(p)] = (agent, "unknown")
    return files


def _extract_project(filepath: str, projects_dir: str) -> str:
    """Extract project name from a Claude Code / AntCC JSONL file path.

    The directory under projects/ encodes the working directory with dashes
    replacing slashes, e.g. '-home-nxw-developer-token-exporter'.
    The encoded path is /home/<user>/<rest>... so we strip the first 3
    segments (home, user, developer) and the remainder is the project name.
    """
    rel = os.path.relpath(filepath, projects_dir)
    project_dir = rel.split(os.sep)[0]
    # Strip leading dash
    stripped = project_dir.lstrip("-")
    parts = stripped.split("-")
    # /home/<user>/<project...> → skip home(0), user(1), developer(2)
    if len(parts) >= 3:
        return "-".join(parts[3:]) or stripped
    return stripped


def _detect_agent(base_dir: str) -> str:
    # Check full path to distinguish between ~/.codex and ~/.codefuse/engine/codex
    if base_dir.endswith("/.codex") or "/.codex/" in base_dir:
        return "codex"
    if "/codex" in base_dir:
        return "antcodex"
    base = os.path.basename(os.path.normpath(base_dir))
    if base.lower() == "cc":
        return "antcc"
    # Match exactly "codefuse" or ".codefuse" (not "codefuse-cc" etc.)
    if base.lower() in ("codefuse", ".codefuse"):
        return "antcc"
    if base.lower() == ".claude" or base == "claude":
        return "cc"
    return "unknown"


def parse_line(line: str) -> dict | None:
    """Parse a Claude Code / AntCC JSONL line into a usage record."""
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


def parse_codex_line(line: str) -> dict | None:
    """Parse a Codex JSONL line.

    Returns a dict with _type 'model' (model update) or 'usage' (token record).
    Codex uses cumulative token counters in total_token_usage, so callers
    must compute deltas between consecutive entries.
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    entry_type = obj.get("type")

    if entry_type == "turn_context":
        payload = obj.get("payload", {})
        model = payload.get("model")
        cwd = payload.get("cwd")
        result = {"_type": "model"}
        if model:
            result["model"] = model
        if cwd:
            result["cwd"] = cwd
        return result if (model or cwd) else None

    if entry_type == "event_msg":
        payload = obj.get("payload", {})
        if payload.get("type") != "token_count":
            return None
        info = payload.get("info")
        if not info:
            return None

        total_usage = info.get("total_token_usage") or {}
        last_usage = info.get("last_token_usage")
        has_last_usage = isinstance(last_usage, dict)
        if not has_last_usage:
            last_usage = {}

        ts_str = obj.get("timestamp", "")
        timestamp = None
        if ts_str:
            try:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        model = info.get("model") or None

        return {
            "_type": "usage",
            "timestamp": timestamp,
            "model": model,
            "total_input_tokens": total_usage.get("input_tokens", 0) or 0,
            "total_output_tokens": total_usage.get("output_tokens", 0) or 0,
            "total_cached_input_tokens": (
                total_usage.get("cached_input_tokens", total_usage.get("cache_read_input_tokens", 0)) or 0
            ),
            "total_reasoning_output_tokens": total_usage.get("reasoning_output_tokens", 0) or 0,
            "has_last_usage": has_last_usage,
            "last_input_tokens": last_usage.get("input_tokens", 0) or 0,
            "last_output_tokens": last_usage.get("output_tokens", 0) or 0,
            "last_cached_input_tokens": (
                last_usage.get("cached_input_tokens", last_usage.get("cache_read_input_tokens", 0)) or 0
            ),
            "last_reasoning_output_tokens": last_usage.get("reasoning_output_tokens", 0) or 0,
        }

    return None


class JSONLWatcher:
    def __init__(self, claude_dirs: list[str], days_back: int = 7, state_file: str = "", on_record=None):
        self.claude_dirs = claude_dirs
        self.days_back = days_back
        self.state_file = state_file
        self.on_record = on_record
        self._file_positions: dict[str, int] = {}
        self._seen_keys: set[str] = set()
        self._codex_state: dict[str, dict] = {}
        self._load_state()

    def scan_history(self):
        """Read existing JSONL files to populate historical data."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days_back)
        files = find_jsonl_files(self.claude_dirs)
        logger.info("Scanning %d JSONL files for history...", len(files))
        count = 0
        for filepath, (agent, project) in files.items():
            pos, n = self._read_file(filepath, agent, project, cutoff)
            self._file_positions[filepath] = pos
            count += n
        logger.info("Scanned %d historical records", count)
        self._save_state()

    def check_updates(self):
        """Check for new lines in known files and any new files."""
        files = find_jsonl_files(self.claude_dirs)

        for filepath, (agent, project) in files.items():
            if filepath not in self._file_positions:
                self._file_positions[filepath] = 0
            pos, _ = self._read_file(filepath, agent, project, None)
            self._file_positions[filepath] = pos

        # Clean up deleted files
        gone = set(self._file_positions.keys()) - set(files.keys())
        for f in gone:
            del self._file_positions[f]
            self._codex_state.pop(f, None)

        self._save_state()

    def _load_state(self):
        if not self.state_file:
            return
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load watcher state from %s: %s", self.state_file, e)
            return

        self._file_positions = {
            str(path): int(pos)
            for path, pos in state.get("file_positions", {}).items()
            if isinstance(pos, int | float)
        }
        self._seen_keys = set(str(key) for key in state.get("seen_keys", []))
        self._codex_state = {
            str(path): value
            for path, value in state.get("codex_state", {}).items()
            if isinstance(value, dict)
        }
        logger.info(
            "Loaded watcher state from %s (%d files, %d dedup keys)",
            self.state_file,
            len(self._file_positions),
            len(self._seen_keys),
        )

    def _save_state(self):
        if not self.state_file:
            return

        state_path = Path(self.state_file)
        tmp_path = state_path.with_name(f"{state_path.name}.tmp")
        state = {
            "version": 1,
            "file_positions": self._file_positions,
            "seen_keys": sorted(self._seen_keys),
            "codex_state": self._codex_state,
        }

        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w") as f:
                json.dump(state, f, separators=(",", ":"), sort_keys=True)
            os.replace(tmp_path, state_path)
        except OSError as e:
            logger.error("Could not save watcher state to %s: %s", self.state_file, e)

    def _read_file(self, filepath: str, agent: str, project: str, cutoff: datetime | None) -> tuple[int, int]:
        """Read new lines from a file starting from tracked position.
        Returns (new_position, record_count)."""
        if agent in ("codex", "antcodex"):
            return self._read_codex_file(filepath, agent, project, cutoff)
        return self._read_claude_file(filepath, agent, project, cutoff)

    def _read_claude_file(self, filepath: str, agent: str, project: str, cutoff: datetime | None) -> tuple[int, int]:
        """Read Claude Code / AntCC JSONL file."""
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

                    record["project"] = project
                    if self.on_record:
                        self.on_record(agent, record)
                    count += 1
                return f.tell(), count
        except OSError as e:
            logger.error("Error reading %s: %s", filepath, e)
            return start_pos, 0

    def _read_codex_file(self, filepath: str, agent: str, project: str, cutoff: datetime | None) -> tuple[int, int]:
        """Read Codex JSONL file with cumulative counter delta computation."""
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

        state = self._codex_state.get(filepath, {
            "model": "unknown",
            "project": "unknown",
            "prev_totals": None,
        })

        try:
            with open(filepath, "r") as f:
                f.seek(start_pos)
                for line in f:
                    parsed = parse_codex_line(line)
                    if parsed is None:
                        continue

                    if parsed["_type"] == "model":
                        if "model" in parsed:
                            state["model"] = parsed["model"]
                        if "cwd" in parsed:
                            state["project"] = os.path.basename(parsed["cwd"])
                        continue

                    model = parsed["model"] or state["model"]

                    cur = {
                        "input": parsed["total_input_tokens"],
                        "output": parsed["total_output_tokens"],
                        "cached": parsed["total_cached_input_tokens"],
                        "reasoning": parsed["total_reasoning_output_tokens"],
                    }

                    prev = state.get("prev_totals")
                    if parsed["has_last_usage"]:
                        delta_input = parsed["last_input_tokens"]
                        delta_output = parsed["last_output_tokens"]
                        delta_cached = parsed["last_cached_input_tokens"]
                        delta_reasoning = parsed["last_reasoning_output_tokens"]
                    elif prev is not None:
                        delta_input = max(cur["input"] - prev["input"], 0)
                        delta_output = max(cur["output"] - prev["output"], 0)
                        delta_cached = max(cur["cached"] - prev["cached"], 0)
                        delta_reasoning = max(cur["reasoning"] - prev["reasoning"], 0)
                    else:
                        delta_input = cur["input"]
                        delta_output = cur["output"]
                        delta_cached = cur["cached"]
                        delta_reasoning = cur["reasoning"]

                    state["prev_totals"] = cur

                    if delta_input <= 0 and delta_output <= 0 and delta_cached <= 0 and delta_reasoning <= 0:
                        continue

                    # Codex input_tokens includes cached_input_tokens, so
                    # subtract cached to get the non-cached (full-price) portion.
                    # This makes input_tokens and cache_read_tokens disjoint,
                    # matching Claude Code/AntCC semantics.
                    input_tokens = max(delta_input, 0)
                    cache_read_tokens = min(max(delta_cached, 0), input_tokens)
                    record = {
                        "timestamp": parsed["timestamp"],
                        "model": model,
                        "project": state["project"],
                        "input_tokens": input_tokens - cache_read_tokens,
                        # Codex reasoning_output_tokens is a breakdown of
                        # output_tokens, not an additional billable amount.
                        "output_tokens": max(delta_output, 0),
                        "cache_creation_tokens": 0,
                        "cache_read_tokens": cache_read_tokens,
                        "cost_usd": 0,
                        "dedup_key": None,
                    }

                    if cutoff and record["timestamp"] and record["timestamp"] < cutoff:
                        continue

                    if self.on_record:
                        self.on_record(agent, record)
                    count += 1

                self._codex_state[filepath] = state
                return f.tell(), count
        except OSError as e:
            logger.error("Error reading %s: %s", filepath, e)
            return start_pos, 0
