import json
import logging
import shlex
import subprocess

logger = logging.getLogger("token-stats")


def _run_command(cmd: list[str], timeout: int = 30) -> dict | None:
    """Run a command and return parsed JSON output, or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning("Command %s exited with code %d: %s", cmd[0], result.returncode, result.stderr.strip())
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logger.error("Command %s timed out after %ds", cmd[0], timeout)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse JSON from %s: %s", cmd[0], e)
    except FileNotFoundError:
        logger.error("Command not found: %s", cmd[0])
    except OSError as e:
        logger.error("Error running %s: %s", cmd[0], e)
    return None


def fetch_cc_blocks(ccusage_bin: str) -> list[dict]:
    """Fetch active 5-hour billing block data using the ccusage CLI.

    Returns a list of block dicts from ``ccusage blocks --json``.
    Returns an empty list when ccusage_bin is not configured or the command fails.
    """
    if not ccusage_bin:
        return []
    cmd = shlex.split(ccusage_bin) + ["blocks", "--json", "--offline"]
    data = _run_command(cmd)
    if data is None:
        return []
    return data.get("blocks") or []


def fetch_codex_daily(ccusage_codex_bin: str) -> list[dict]:
    """Fetch today's Codex usage data using the @ccusage/codex CLI.

    Returns a list of daily row dicts from ``@ccusage/codex daily --json``.
    Returns an empty list when ccusage_codex_bin is not configured or the command fails.
    """
    if not ccusage_codex_bin:
        return []
    cmd = shlex.split(ccusage_codex_bin) + ["daily", "--json", "--offline"]
    data = _run_command(cmd)
    if data is None:
        return []
    return data.get("daily") or []
