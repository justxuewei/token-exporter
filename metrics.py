import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Gauge

logger = logging.getLogger("token-stats")

LABELS = ["source", "agent", "model"]
DAILY_LABELS = ["source", "agent", "model", "date"]
BLOCK_LABELS = ["source", "agent"]
PLAN_LABELS = ["source", "agent", "plan"]

# JSONL agent names that belong to the "cc" family for rate-limit purposes
_CC_AGENTS = frozenset({"claude-code", "antcc"})

input_tokens_total = Counter("codeagent_input_tokens_total", "Total input tokens", LABELS)
output_tokens_total = Counter("codeagent_output_tokens_total", "Total output tokens", LABELS)
cache_creation_tokens_total = Counter("codeagent_cache_creation_tokens_total", "Total cache creation tokens", LABELS)
cache_read_tokens_total = Counter("codeagent_cache_read_tokens_total", "Total cache read tokens", LABELS)
cost_usd_total = Counter("codeagent_cost_usd_total", "Total cost in USD", LABELS)

daily_input_tokens = Gauge("codeagent_daily_input_tokens", "Daily input tokens", DAILY_LABELS)
daily_output_tokens = Gauge("codeagent_daily_output_tokens", "Daily output tokens", DAILY_LABELS)
daily_cache_creation_tokens = Gauge("codeagent_daily_cache_creation_tokens", "Daily cache creation tokens", DAILY_LABELS)
daily_cache_read_tokens = Gauge("codeagent_daily_cache_read_tokens", "Daily cache read tokens", DAILY_LABELS)
daily_cost_usd = Gauge("codeagent_daily_cost_usd", "Daily cost USD", DAILY_LABELS)

# Rate-limit / billing-block metrics (5-hour windows for CC, daily for Codex)
block_input_tokens = Gauge("codeagent_block_input_tokens", "Input tokens in the current billing block", BLOCK_LABELS)
block_output_tokens = Gauge("codeagent_block_output_tokens", "Output tokens in the current billing block", BLOCK_LABELS)
block_cache_creation_tokens = Gauge("codeagent_block_cache_creation_tokens", "Cache creation tokens in the current billing block", BLOCK_LABELS)
block_cache_read_tokens = Gauge("codeagent_block_cache_read_tokens", "Cache read tokens in the current billing block", BLOCK_LABELS)
block_total_tokens = Gauge("codeagent_block_total_tokens", "Total tokens in the current billing block", BLOCK_LABELS)
block_cost_usd = Gauge("codeagent_block_cost_usd", "Cost in USD in the current billing block", BLOCK_LABELS)
block_burn_rate_tokens_per_minute = Gauge("codeagent_block_burn_rate_tokens_per_minute", "Token burn rate in the current billing block (tokens/min)", BLOCK_LABELS)
block_projected_total_tokens = Gauge("codeagent_block_projected_total_tokens", "Projected total tokens for the current billing block", BLOCK_LABELS)
block_is_active = Gauge("codeagent_block_is_active", "1 if a billing block is currently active, 0 otherwise", BLOCK_LABELS)
block_end_time_seconds = Gauge("codeagent_block_end_time_seconds", "Unix timestamp of billing block end (0 if none)", BLOCK_LABELS)
block_limit_tokens = Gauge("codeagent_block_limit_tokens", "Configured token limit per billing block (0 if not set)", BLOCK_LABELS)

# Weekly usage metrics (current Mon–Sun calendar week)
week_total_tokens = Gauge("codeagent_week_total_tokens", "Total tokens used in the current calendar week", BLOCK_LABELS)
week_cost_usd = Gauge("codeagent_week_cost_usd", "Cost in USD in the current calendar week", BLOCK_LABELS)
week_limit_tokens = Gauge("codeagent_week_limit_tokens", "Configured weekly token limit (0 if not set)", BLOCK_LABELS)
week_reset_time_seconds = Gauge("codeagent_week_reset_time_seconds", "Unix timestamp of next weekly reset (next Monday midnight UTC)", BLOCK_LABELS)

# Plan info gauge — value=1, use the 'plan' label to display the plan name
plan_info = Gauge("codeagent_plan_info", "Billing plan information (value=1, read the 'plan' label for the name)", PLAN_LABELS)

_daily_data: dict[tuple, float] = defaultdict(lambda: {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0, "cost": 0.0})

_source: str = ""
_plan_cache: dict[str, str] = {}


def set_source(source: str):
    global _source
    _source = source


def record_usage(agent: str, rec: dict):
    model = rec["model"]
    src = _source
    input_tokens_total.labels(source=src, agent=agent, model=model).inc(rec["input_tokens"])
    output_tokens_total.labels(source=src, agent=agent, model=model).inc(rec["output_tokens"])
    if rec["cache_creation_tokens"] > 0:
        cache_creation_tokens_total.labels(source=src, agent=agent, model=model).inc(rec["cache_creation_tokens"])
    if rec["cache_read_tokens"] > 0:
        cache_read_tokens_total.labels(source=src, agent=agent, model=model).inc(rec["cache_read_tokens"])
    if rec["cost_usd"] > 0:
        cost_usd_total.labels(source=src, agent=agent, model=model).inc(rec["cost_usd"])

    # Accumulate daily gauge data
    ts = rec.get("timestamp")
    date_str = ts.strftime("%Y-%m-%d") if ts else "unknown"
    key = (src, agent, model, date_str)
    d = _daily_data[key]
    d["input"] += rec["input_tokens"]
    d["output"] += rec["output_tokens"]
    d["cache_creation"] += rec["cache_creation_tokens"]
    d["cache_read"] += rec["cache_read_tokens"]
    d["cost"] += rec["cost_usd"]

    # Update daily gauges
    daily_input_tokens.labels(source=src, agent=agent, model=model, date=date_str).set(d["input"])
    daily_output_tokens.labels(source=src, agent=agent, model=model, date=date_str).set(d["output"])
    daily_cache_creation_tokens.labels(source=src, agent=agent, model=model, date=date_str).set(d["cache_creation"])
    daily_cache_read_tokens.labels(source=src, agent=agent, model=model, date=date_str).set(d["cache_read"])
    daily_cost_usd.labels(source=src, agent=agent, model=model, date=date_str).set(d["cost"])


def _zero_block_metrics(src: str, agent: str) -> None:
    """Set all billing-block gauges to zero for the given agent."""
    block_input_tokens.labels(source=src, agent=agent).set(0)
    block_output_tokens.labels(source=src, agent=agent).set(0)
    block_cache_creation_tokens.labels(source=src, agent=agent).set(0)
    block_cache_read_tokens.labels(source=src, agent=agent).set(0)
    block_total_tokens.labels(source=src, agent=agent).set(0)
    block_cost_usd.labels(source=src, agent=agent).set(0)
    block_burn_rate_tokens_per_minute.labels(source=src, agent=agent).set(0)
    block_projected_total_tokens.labels(source=src, agent=agent).set(0)
    block_is_active.labels(source=src, agent=agent).set(0)
    block_end_time_seconds.labels(source=src, agent=agent).set(0)


def record_cc_blocks(blocks: list[dict], block_limit: int = 0):
    """Update billing-block rate-limit metrics from ``ccusage blocks --json`` output.

    Only the most recent active block (or the last completed block when no
    active block exists) is used to set the gauge values.  All metrics are
    zeroed when *blocks* is empty so that the dashboard shows 0 rather than
    stale values when CC is unavailable.
    """
    src = _source
    agent = "cc"

    block_limit_tokens.labels(source=src, agent=agent).set(block_limit)

    active = [b for b in blocks if b.get("isActive")]
    if active:
        relevant = active
    elif blocks:
        relevant = blocks[-1:]
    else:
        relevant = []

    if not relevant:
        _zero_block_metrics(src, agent)
        return

    block = relevant[0]
    is_active = 1 if block.get("isActive") else 0
    token_counts = block.get("tokenCounts") or {}
    burn = block.get("burnRate") or {}
    proj = block.get("projection") or {}

    end_ts = 0.0
    end_time_str = block.get("endTime", "")
    if end_time_str:
        try:
            end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
            end_ts = end_dt.timestamp()
        except (ValueError, TypeError):
            pass

    block_input_tokens.labels(source=src, agent=agent).set(token_counts.get("inputTokens", 0))
    block_output_tokens.labels(source=src, agent=agent).set(token_counts.get("outputTokens", 0))
    block_cache_creation_tokens.labels(source=src, agent=agent).set(token_counts.get("cacheCreationInputTokens", 0))
    block_cache_read_tokens.labels(source=src, agent=agent).set(token_counts.get("cacheReadInputTokens", 0))
    block_total_tokens.labels(source=src, agent=agent).set(block.get("totalTokens", 0))
    block_cost_usd.labels(source=src, agent=agent).set(block.get("costUSD", 0))
    block_burn_rate_tokens_per_minute.labels(source=src, agent=agent).set(burn.get("tokensPerMinute", 0))
    block_projected_total_tokens.labels(source=src, agent=agent).set(proj.get("totalTokens", 0))
    block_is_active.labels(source=src, agent=agent).set(is_active)
    block_end_time_seconds.labels(source=src, agent=agent).set(end_ts)


def record_codex_daily(daily_rows: list[dict], block_limit: int = 0):
    """Update billing-block rate-limit metrics from ``@ccusage/codex daily --json`` output.

    Only today's row is used so that the gauges reflect the current day's usage
    against Codex rate limits.  All metrics are zeroed when *daily_rows* is
    empty so that the dashboard shows 0 when Codex is unavailable.
    """
    src = _source
    agent = "codex"

    # Codex "block" resets at midnight UTC each day
    now_utc = datetime.now(timezone.utc)
    next_midnight = (now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    end_ts = next_midnight.timestamp()

    block_limit_tokens.labels(source=src, agent=agent).set(block_limit)
    block_end_time_seconds.labels(source=src, agent=agent).set(end_ts)

    today = now_utc.strftime("%Y-%m-%d")
    today_rows = [r for r in daily_rows if r.get("date") == today]
    row = today_rows[0] if today_rows else (daily_rows[-1] if daily_rows else None)

    if row is None:
        _zero_block_metrics(src, agent)
        # Restore the end-time already set above (zeroed by _zero_block_metrics)
        block_end_time_seconds.labels(source=src, agent=agent).set(end_ts)
        return

    block_input_tokens.labels(source=src, agent=agent).set(row.get("inputTokens", 0))
    block_output_tokens.labels(source=src, agent=agent).set(row.get("outputTokens", 0))
    block_cache_creation_tokens.labels(source=src, agent=agent).set(0)
    block_cache_read_tokens.labels(source=src, agent=agent).set(row.get("cachedInputTokens", 0))
    block_total_tokens.labels(source=src, agent=agent).set(row.get("totalTokens", 0))
    block_cost_usd.labels(source=src, agent=agent).set(row.get("costUSD", 0))
    block_burn_rate_tokens_per_minute.labels(source=src, agent=agent).set(0)
    block_projected_total_tokens.labels(source=src, agent=agent).set(0)
    block_is_active.labels(source=src, agent=agent).set(1 if row.get("date") == today else 0)


def record_weekly_totals(cc_week_limit: int = 0, codex_week_limit: int = 0):
    """Aggregate *_daily_data* for the current Mon–Sun calendar week and update
    ``codeagent_week_*`` gauges.

    JSONL agents belonging to the CC family (``claude-code``, ``antcc``) are
    collapsed into the ``"cc"`` agent label to stay consistent with the block
    metrics emitted by :func:`record_cc_blocks`.
    """
    src = _source
    now_utc = datetime.now(timezone.utc)

    # Monday midnight UTC at the start of the current week
    week_monday = (now_utc - timedelta(days=now_utc.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_dates = {(week_monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)}

    # Next Monday midnight UTC is the weekly reset point
    next_monday = week_monday + timedelta(days=7)
    week_reset_ts = next_monday.timestamp()

    totals: dict[str, dict] = {
        "cc": {"tokens": 0.0, "cost": 0.0},
        "codex": {"tokens": 0.0, "cost": 0.0},
    }

    for (source, agent, _model, date), data in _daily_data.items():
        if date not in week_dates:
            continue
        block_agent = "cc" if agent in _CC_AGENTS else agent
        if block_agent not in totals:
            continue
        totals[block_agent]["tokens"] += (
            data["input"] + data["output"] + data["cache_creation"] + data["cache_read"]
        )
        totals[block_agent]["cost"] += data["cost"]

    limits = {"cc": cc_week_limit, "codex": codex_week_limit}
    for block_agent, data in totals.items():
        week_total_tokens.labels(source=src, agent=block_agent).set(data["tokens"])
        week_cost_usd.labels(source=src, agent=block_agent).set(data["cost"])
        week_limit_tokens.labels(source=src, agent=block_agent).set(limits[block_agent])
        week_reset_time_seconds.labels(source=src, agent=block_agent).set(week_reset_ts)


def record_plan_info(agent: str, plan: str):
    """Set the plan info gauge for *agent*.

    Uses a cache to clear the old label series whenever the plan string changes,
    preventing stale ``{plan="old-plan"}=1`` time series from lingering.
    """
    src = _source
    old_plan = _plan_cache.get(agent, "")
    if old_plan and old_plan != plan:
        plan_info.labels(source=src, agent=agent, plan=old_plan).set(0)
    _plan_cache[agent] = plan
    if plan:
        plan_info.labels(source=src, agent=agent, plan=plan).set(1)