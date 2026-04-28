import logging
from collections import defaultdict
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge

logger = logging.getLogger("token-stats")

LABELS = ["source", "agent", "project", "model"]
DAILY_LABELS = ["source", "agent", "project", "model", "date"]

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

_daily_data: dict[tuple, float] = defaultdict(lambda: {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0, "cost": 0.0})

_source: str = ""


def set_source(source: str):
    global _source
    _source = source


def record_usage(agent: str, rec: dict):
    model = rec["model"]
    project = rec.get("project", "unknown")
    src = _source
    input_tokens_total.labels(source=src, agent=agent, project=project, model=model).inc(rec["input_tokens"])
    output_tokens_total.labels(source=src, agent=agent, project=project, model=model).inc(rec["output_tokens"])
    if rec["cache_creation_tokens"] > 0:
        cache_creation_tokens_total.labels(source=src, agent=agent, project=project, model=model).inc(rec["cache_creation_tokens"])
    if rec["cache_read_tokens"] > 0:
        cache_read_tokens_total.labels(source=src, agent=agent, project=project, model=model).inc(rec["cache_read_tokens"])
    if rec["cost_usd"] > 0:
        cost_usd_total.labels(source=src, agent=agent, project=project, model=model).inc(rec["cost_usd"])

    # Accumulate daily gauge data
    ts = rec.get("timestamp")
    date_str = ts.strftime("%Y-%m-%d") if ts else "unknown"
    key = (src, agent, project, model, date_str)
    d = _daily_data[key]
    d["input"] += rec["input_tokens"]
    d["output"] += rec["output_tokens"]
    d["cache_creation"] += rec["cache_creation_tokens"]
    d["cache_read"] += rec["cache_read_tokens"]
    d["cost"] += rec["cost_usd"]

    # Update daily gauges
    daily_input_tokens.labels(source=src, agent=agent, project=project, model=model, date=date_str).set(d["input"])
    daily_output_tokens.labels(source=src, agent=agent, project=project, model=model, date=date_str).set(d["output"])
    daily_cache_creation_tokens.labels(source=src, agent=agent, project=project, model=model, date=date_str).set(d["cache_creation"])
    daily_cache_read_tokens.labels(source=src, agent=agent, project=project, model=model, date=date_str).set(d["cache_read"])
    daily_cost_usd.labels(source=src, agent=agent, project=project, model=model, date=date_str).set(d["cost"])