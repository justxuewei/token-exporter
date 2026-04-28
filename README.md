# Token Exporter

A Prometheus exporter that watches Claude Code, AntCC, and Codex JSONL conversation files and exposes token usage metrics.

## Features

- Tracks input, output, cache creation, and cache read tokens per agent and model
- Tracks cost in USD
- Supports multiple agents: Claude Code, AntCC (CodeFuse), Codex
- Configurable source label for multi-machine setups
- Daily token gauges for historical queries
- Grafana dashboard included

## Quick Start

```bash
# Build and push
make
make push

# Run locally
docker run -d \
  --name token-exporter \
  --net host \
  -v ~/.claude:/root/.claude:ro \
  -v ~/.codefuse:/root/.codefuse:ro \
  -v ~/.codex:/root/.codex:ro \
  -e SOURCE=devhome \
  xavierniu/token-exporter:latest
```

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `LISTEN_PORT` | `14531` | Prometheus metrics port |
| `WATCH_INTERVAL` | `5` | Seconds between file checks |
| `CLAUDE_CONFIG_DIR` | `~/.claude,~/.codefuse/engine/cc,~/.codex` | Comma-separated config directories |
| `DAYS_BACK` | `7` | Days of history to scan on startup |
| `SOURCE` | `""` | Source label for multi-machine setups |
| `CCUSAGE_BIN` | `""` | Path/command for `ccusage` CLI (e.g. `npx ccusage@latest`). Enables CC rate-limit block metrics when set. |
| `CCUSAGE_CODEX_BIN` | `""` | Path/command for `@ccusage/codex` CLI (e.g. `npx @ccusage/codex@latest`). Enables Codex rate-limit metrics when set. |

## Metrics

| Metric | Type | Labels |
|---|---|---|
| `codeagent_input_tokens_total` | Counter | source, agent, model |
| `codeagent_output_tokens_total` | Counter | source, agent, model |
| `codeagent_cache_creation_tokens_total` | Counter | source, agent, model |
| `codeagent_cache_read_tokens_total` | Counter | source, agent, model |
| `codeagent_cost_usd_total` | Counter | source, agent, model |
| `codeagent_daily_input_tokens` | Gauge | source, agent, model, date |
| `codeagent_daily_output_tokens` | Gauge | source, agent, model, date |
| `codeagent_daily_cache_creation_tokens` | Gauge | source, agent, model, date |
| `codeagent_daily_cache_read_tokens` | Gauge | source, agent, model, date |
| `codeagent_daily_cost_usd` | Gauge | source, agent, model, date |

## Rate-Limit / Billing-Block Metrics

These metrics are populated when `CCUSAGE_BIN` or `CCUSAGE_CODEX_BIN` are configured.
They expose the current 5-hour billing window (CC) or today's usage (Codex) for rate-limit monitoring.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `codeagent_block_input_tokens` | Gauge | source, agent | Input tokens in the current billing block |
| `codeagent_block_output_tokens` | Gauge | source, agent | Output tokens in the current billing block |
| `codeagent_block_cache_creation_tokens` | Gauge | source, agent | Cache-creation tokens in the current billing block |
| `codeagent_block_cache_read_tokens` | Gauge | source, agent | Cache-read tokens in the current billing block |
| `codeagent_block_total_tokens` | Gauge | source, agent | Total tokens in the current billing block |
| `codeagent_block_cost_usd` | Gauge | source, agent | Cost in USD in the current billing block |
| `codeagent_block_burn_rate_tokens_per_minute` | Gauge | source, agent | Token burn rate (CC only, 0 for Codex) |
| `codeagent_block_projected_total_tokens` | Gauge | source, agent | Projected total tokens for the block (CC only) |
| `codeagent_block_is_active` | Gauge | source, agent | 1 if a billing block is currently active |

## Grafana Dashboard

Import `grafana/dashboards/token-stats.json` or use the included provisioning. The dashboard includes:

- **Token Usage** — stacked timeseries of input/output/cache read/cache creation rates
- **Cache Hit Rate** — `cache_read / (cache_read + input)` over time
- **Summary stats** — total tokens, input, output, cache, and cache hit rate for the selected time range
- Filterable by **source** and **agent**

## Docker Compose

```bash
docker compose up -d
```

This starts the exporter, Prometheus, and Grafana together.