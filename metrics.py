import logging
from collections import defaultdict
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge

logger = logging.getLogger("token-stats")

LABELS = ["source", "agent", "model"]
DAILY_LABELS = ["source", "agent", "model", "date"]
BLOCK_LABELS = ["source", "agent"]

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

_daily_data: dict[tuple, float] = defaultdict(lambda: {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0, "cost": 0.0})

_source: str = ""


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


def record_cc_blocks(blocks: list[dict]):
    """Update billing-block rate-limit metrics from ``ccusage blocks --json`` output.

    Only the most recent active block (or the last completed block when no
    active block exists) is used to set the gauge values.
    """
    src = _source
    agent = "cc"

    active = [b for b in blocks if b.get("isActive")]
    if active:
        relevant = active
    elif blocks:
        relevant = blocks[-1:]
    else:
        relevant = []

    if not relevant:
        block_is_active.labels(source=src, agent=agent).set(0)
        return

    block = relevant[0]
    is_active = 1 if block.get("isActive") else 0
    token_counts = block.get("tokenCounts") or {}
    burn = block.get("burnRate") or {}
    proj = block.get("projection") or {}

    block_input_tokens.labels(source=src, agent=agent).set(token_counts.get("inputTokens", 0))
    block_output_tokens.labels(source=src, agent=agent).set(token_counts.get("outputTokens", 0))
    block_cache_creation_tokens.labels(source=src, agent=agent).set(token_counts.get("cacheCreationInputTokens", 0))
    block_cache_read_tokens.labels(source=src, agent=agent).set(token_counts.get("cacheReadInputTokens", 0))
    block_total_tokens.labels(source=src, agent=agent).set(block.get("totalTokens", 0))
    block_cost_usd.labels(source=src, agent=agent).set(block.get("costUSD", 0))
    block_burn_rate_tokens_per_minute.labels(source=src, agent=agent).set(burn.get("tokensPerMinute", 0))
    block_projected_total_tokens.labels(source=src, agent=agent).set(proj.get("totalTokens", 0))
    block_is_active.labels(source=src, agent=agent).set(is_active)


def record_codex_daily(daily_rows: list[dict]):
    """Update billing-block rate-limit metrics from ``@ccusage/codex daily --json`` output.

    Only today's row is used so that the gauges reflect the current day's usage
    against Codex rate limits.
    """
    src = _source
    agent = "codex"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_rows = [r for r in daily_rows if r.get("date") == today]
    row = today_rows[0] if today_rows else (daily_rows[-1] if daily_rows else None)

    if row is None:
        block_is_active.labels(source=src, agent=agent).set(0)
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