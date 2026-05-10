import json
import logging
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("token-stats")


def _detect_agent(base_dir: str) -> str:
    """Detect agent type from the base directory path."""
    if base_dir.endswith("/.codex") or "/.codex/" in base_dir:
        return "codex"
    if "/codex" in base_dir:
        return "antcodex"
    base = os.path.basename(os.path.normpath(base_dir))
    if base.lower() == "cc":
        return "antcc"
    if base.lower() in ("codefuse", ".codefuse"):
        return "antcc"
    if base.lower() == ".claude" or base == "claude":
        return "cc"
    return "unknown"


def _extract_project(project_key: str) -> str:
    """Extract project name from a ccusage --instances project key or directory path.

    The key encodes the working directory with dashes replacing slashes,
    e.g. '-home-nxw-developer-token-exporter'.
    Strip the first 3 segments (home, user, developer) and the remainder
    is the project name.
    """
    stripped = project_key.lstrip("-")
    parts = stripped.split("-")
    if len(parts) >= 3:
        return "-".join(parts[3:]) or stripped
    return stripped


def _run_ccusage(claude_dir: str, since: date, timezone_name: str = "UTC") -> dict | None:
    """Run ccusage daily --json --offline --timezone <tz> --instances and return parsed JSON."""
    cmd = [
        "ccusage", "daily", "--json", "--offline",
        "--timezone", timezone_name,
        "--instances",
        "--since", since.strftime("%Y%m%d"),
    ]
    env = {**os.environ, "CLAUDE_CONFIG_DIR": claude_dir}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        if result.returncode != 0:
            logger.error("ccusage failed for %s: %s", claude_dir, result.stderr.strip())
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("ccusage error for %s: %s", claude_dir, e)
        return None


def _run_codex_usage(codex_dir: str, since: date, timezone_name: str = "UTC") -> dict | None:
    """Run @ccusage/codex daily --json --offline --timezone <tz> and return parsed JSON."""
    cmd = [
        "npx", "@ccusage/codex", "daily", "--json", "--offline",
        "--timezone", timezone_name,
        "--since", since.strftime("%Y-%m-%d"),
    ]
    env = {**os.environ, "CODEX_HOME": codex_dir}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        if result.returncode != 0:
            logger.error("ccusage codex failed for %s: %s", codex_dir, result.stderr.strip())
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("ccusage codex error for %s: %s", codex_dir, e)
        return None


def _scan_antcc_dir(base_dir: str, since: date, timezone_name: str = "UTC") -> dict:
    """Scan AntCC JSONL files directly with msg_id dedup.

    AntCC records have empty requestId, so ccusage doesn't dedup them
    (https://github.com/ryoppippi/ccusage/issues/976). This parser
    deduplicates by msg_id to produce correct counts.

    Returns dict in ccusage --instances format:
    {"projects": {"-home-...": [{"date": "...", "modelBreakdowns": [...]}]}}
    """
    tz = ZoneInfo(timezone_name)
    since_local = since.strftime("%Y-%m-%d")
    projects_dir = os.path.join(base_dir, "projects")

    # Aggregate per (project_key, date, model) with dedup by msg_id
    # {project_key: {date: {model: {"input": N, "output": N, ...}}}}
    agg: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    # Per-msg_id token values for dedup: {project_key: {date: {msg_id: {"input": N, ...}}}}
    msg_values: dict[str, dict[str, dict[str, dict[str, int]]]] = {}

    for project_dir in Path(projects_dir).iterdir():
        if not project_dir.is_dir():
            continue
        project_key = project_dir.name
        agg[project_key] = {}
        msg_values[project_key] = {}

        for jsonl_file in project_dir.rglob("*.jsonl"):
            try:
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

                        msg_id = obj.get("message", {}).get("id", "")
                        ts_str = obj.get("timestamp", "")
                        if not ts_str:
                            continue

                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            continue

                        local_ts = ts.astimezone(tz)
                        date_str = local_ts.strftime("%Y-%m-%d")
                        if date_str < since_local:
                            continue

                        model = obj.get("message", {}).get("model", "unknown") or "unknown"
                        input_tokens = usage.get("input_tokens", 0) or 0
                        output_tokens = usage.get("output_tokens", 0) or 0
                        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
                        cache_read = usage.get("cache_read_input_tokens", 0) or 0

                        if not input_tokens and not output_tokens and not cache_creation and not cache_read:
                            continue

                        date_agg = agg[project_key].setdefault(date_str, {})
                        if model not in date_agg:
                            date_agg[model] = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}
                        m = date_agg[model]

                        # Dedup by msg_id: subtract old values, add new (streaming updates)
                        if msg_id:
                            date_msgs = msg_values[project_key].setdefault(date_str, {})
                            old = date_msgs.get(msg_id)
                            if old:
                                m["input"] -= old["input"]
                                m["output"] -= old["output"]
                                m["cache_creation"] -= old["cache_creation"]
                                m["cache_read"] -= old["cache_read"]
                            date_msgs[msg_id] = {"input": input_tokens, "output": output_tokens, "cache_creation": cache_creation, "cache_read": cache_read}

                        m["input"] += input_tokens
                        m["output"] += output_tokens
                        m["cache_creation"] += cache_creation
                        m["cache_read"] += cache_read
            except OSError:
                continue

    # Convert to ccusage --instances format
    result: dict[str, list] = {"projects": {}}
    for project_key, dates in sorted(agg.items()):
        entries = []
        for date_str, models in sorted(dates.items()):
            breakdowns = []
            for model, tokens in sorted(models.items()):
                breakdowns.append({
                    "modelName": model,
                    "inputTokens": tokens["input"],
                    "outputTokens": tokens["output"],
                    "cacheCreationTokens": tokens["cache_creation"],
                    "cacheReadTokens": tokens["cache_read"],
                    "cost": 0,
                })
            entries.append({
                "date": date_str,
                "inputTokens": sum(b["inputTokens"] for b in breakdowns),
                "outputTokens": sum(b["outputTokens"] for b in breakdowns),
                "cacheCreationTokens": sum(b["cacheCreationTokens"] for b in breakdowns),
                "cacheReadTokens": sum(b["cacheReadTokens"] for b in breakdowns),
                "totalTokens": sum(b["inputTokens"] + b["outputTokens"] + b["cacheCreationTokens"] + b["cacheReadTokens"] for b in breakdowns),
                "totalCost": 0,
                "modelsUsed": [b["modelName"] for b in breakdowns],
                "modelBreakdowns": breakdowns,
            })
        if entries:
            result["projects"][project_key] = entries
    return result


# Token fields to track for delta computation
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")


def _load_last_totals(path: str) -> dict[tuple, dict]:
    """Load last-seen totals from a JSON state file."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not load state from %s: %s", path, e)
        return {}
    totals = {}
    for key_str, values in data.get("last_totals", {}).items():
        key = tuple(key_str.split("\x00"))
        totals[key] = {k: v for k, v in values.items() if k in _TOKEN_FIELDS}
    return totals


def _save_last_totals(path: str, totals: dict[tuple, dict]) -> None:
    """Persist last-seen totals to a JSON state file."""
    state_path = Path(path)
    tmp_path = state_path.with_name(f"{state_path.name}.tmp")
    serializable = {
        "last_totals": {
            "\x00".join(key): values for key, values in totals.items()
        }
    }
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(serializable, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmp_path, state_path)
    except OSError as e:
        logger.error("Could not save state to %s: %s", path, e)


def _prune_stale_totals(totals: dict[tuple, dict], cutoff: date) -> None:
    """Remove entries with dates older than cutoff."""
    stale_keys = [k for k in totals if len(k) >= 4 and k[3] < cutoff.isoformat()]
    for k in stale_keys:
        del totals[k]


def _process_ccusage_data(data: dict, agent: str, on_record, last_totals: dict, project: str = "") -> int:
    """Parse ccusage JSON output and emit delta records.

    Handles two formats:
    - --instances: {"projects": {"key": [entries]}}
    - daily: {"daily": [entries]}
    """
    count = 0

    # Normalize to list of (project, entries) pairs
    if "projects" in data:
        items = [(_extract_project(k), entries) for k, entries in data["projects"].items()]
    elif "daily" in data:
        items = [(project or "unknown", data["daily"])]
    else:
        return 0

    for project, entries in items:
        for entry in entries:
            date_str = entry.get("date", "")
            for bd in entry.get("modelBreakdowns", []):
                model = bd.get("modelName", "unknown")
                current = {
                    "input_tokens": bd.get("inputTokens", 0),
                    "output_tokens": bd.get("outputTokens", 0),
                    "cache_creation_tokens": bd.get("cacheCreationTokens", 0),
                    "cache_read_tokens": bd.get("cacheReadTokens", 0),
                }
                key = (agent, project, model, date_str)
                last = last_totals.get(key)
                if last is None:
                    delta = current
                else:
                    delta = {
                        field: max(current[field] - last.get(field, 0), 0)
                        for field in _TOKEN_FIELDS
                    }
                last_totals[key] = current

                has_delta = any(delta.get(f, 0) > 0 for f in _TOKEN_FIELDS)
                if not has_delta:
                    continue

                record = {
                    "timestamp": datetime.fromisoformat(date_str) if date_str else None,
                    "model": model,
                    "project": project,
                    "input_tokens": delta["input_tokens"],
                    "output_tokens": delta["output_tokens"],
                    "cache_creation_tokens": delta["cache_creation_tokens"],
                    "cache_read_tokens": delta["cache_read_tokens"],
                    "cost_usd": bd.get("cost", 0) or 0,
                }
                if on_record:
                    on_record(agent, record)
                count += 1
    return count


class CcusageCollector:
    """Collects token usage from Claude Code / AntCC data directories.

    Uses ccusage CLI for Claude Code (cc) directories and inline JSONL
    parsing with msg_id dedup for AntCC directories (ccusage doesn't
    dedup when requestId is empty).
    """

    def __init__(self, claude_dirs: list[str], days_back: int = 7, on_record=None, state_file: str = "", timezone_name: str = "UTC"):
        self.claude_dirs = claude_dirs
        self.days_back = days_back
        self.on_record = on_record
        self.state_file = state_file
        self.timezone_name = timezone_name
        self._last_totals: dict[tuple, dict[str, float]] = {}
        if state_file:
            self._last_totals = _load_last_totals(state_file)
            logger.info("Loaded CcusageCollector state from %s (%d entries)", state_file, len(self._last_totals))

    def scan_history(self):
        """Scan all configured directories and emit records."""
        since = (datetime.now(timezone.utc) - timedelta(days=self.days_back)).date()
        _prune_stale_totals(self._last_totals, since)
        total_records = 0
        for claude_dir in self.claude_dirs:
            if not os.path.isdir(claude_dir):
                continue
            if not os.path.isdir(os.path.join(claude_dir, "projects")):
                logger.debug("Skipping %s: no projects/ directory", claude_dir)
                continue
            agent = _detect_agent(claude_dir)
            if agent == "antcc":
                data = _scan_antcc_dir(claude_dir, since, self.timezone_name)
                total_records += _process_ccusage_data(data, agent, self.on_record, self._last_totals)
            else:
                data = _run_ccusage(claude_dir, since, self.timezone_name)
                if data is None:
                    continue
                total_records += _process_ccusage_data(data, agent, self.on_record, self._last_totals)
        if self.state_file:
            _save_last_totals(self.state_file, self._last_totals)
        logger.info("CcusageCollector: emitted %d records", total_records)

    def check_updates(self):
        """Re-scan for new data (same as scan_history)."""
        self.scan_history()


class CodexCollector:
    """Collects token usage from Codex data directories via @ccusage/codex CLI."""

    def __init__(self, codex_dirs: list[str], days_back: int = 7, on_record=None, state_file: str = "", timezone_name: str = "UTC"):
        self.codex_dirs = codex_dirs
        self.days_back = days_back
        self.on_record = on_record
        self.state_file = state_file
        self.timezone_name = timezone_name
        self._last_totals: dict[tuple, dict[str, float]] = {}
        if state_file:
            self._last_totals = _load_last_totals(state_file)
            logger.info("Loaded CodexCollector state from %s (%d entries)", state_file, len(self._last_totals))

    def scan_history(self):
        """Run @ccusage/codex for all configured directories and emit records."""
        since = (datetime.now(timezone.utc) - timedelta(days=self.days_back)).date()
        _prune_stale_totals(self._last_totals, since)
        total_records = 0
        for codex_dir in self.codex_dirs:
            if not os.path.isdir(codex_dir):
                continue
            if not os.path.isdir(os.path.join(codex_dir, "sessions")):
                logger.debug("Skipping %s: no sessions/ directory", codex_dir)
                continue
            agent = _detect_agent(codex_dir)
            data = _run_codex_usage(codex_dir, since, self.timezone_name)
            if data is None:
                continue
            total_records += _process_ccusage_data(data, agent, self.on_record, self._last_totals)
        if self.state_file:
            _save_last_totals(self.state_file, self._last_totals)
        logger.info("CodexCollector: emitted %d records", total_records)

    def check_updates(self):
        """Re-scan for new data (same as scan_history)."""
        self.scan_history()