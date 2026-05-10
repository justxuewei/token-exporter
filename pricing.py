import json
import logging
import os
import urllib.request
from pathlib import Path
from time import time

logger = logging.getLogger("token-stats")

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
DEFAULT_TTL_SECS = 24 * 3600
_RATE_FIELDS = ("input", "output", "cached", "cache_creation")


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return {}


def _fetch_litellm(timeout: float = 30.0) -> dict | None:
    try:
        with urllib.request.urlopen(LITELLM_URL, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Could not fetch LiteLLM pricing: %s", e)
        return None


def _refresh_cache(cache_path: str, ttl: int) -> dict:
    p = Path(cache_path)
    if p.exists() and (time() - p.stat().st_mtime) < ttl:
        return _load_json(cache_path)
    data = _fetch_litellm()
    if data is None:
        return _load_json(cache_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f)
        logger.info("Refreshed LiteLLM pricing cache (%d models)", len(data))
    except OSError as e:
        logger.warning("Could not write pricing cache %s: %s", cache_path, e)
    return data


def _normalize_litellm(litellm_data: dict) -> dict:
    """Convert LiteLLM per-token rates into per-million-token rates."""
    out: dict[str, dict[str, float]] = {}
    for name, spec in litellm_data.items():
        if not isinstance(spec, dict):
            continue
        ipt = spec.get("input_cost_per_token")
        opt = spec.get("output_cost_per_token")
        if ipt is None and opt is None:
            continue
        ipt = ipt or 0
        opt = opt or 0
        cached = spec.get("cache_read_input_token_cost", ipt)
        cache_creation = spec.get("cache_creation_input_token_cost", ipt)
        out[name] = {
            "input": ipt * 1_000_000,
            "output": opt * 1_000_000,
            "cached": (cached or 0) * 1_000_000,
            "cache_creation": (cache_creation or 0) * 1_000_000,
        }
    return out


def load_pricing(override_path: str = "", cache_path: str = "", ttl: int = DEFAULT_TTL_SECS) -> dict:
    """Load pricing rates per model. Local override takes precedence over LiteLLM."""
    rates: dict[str, dict[str, float]] = {}
    if cache_path:
        rates.update(_normalize_litellm(_refresh_cache(cache_path, ttl)))
    if override_path:
        for name, spec in _load_json(override_path).get("models", {}).items():
            if isinstance(spec, dict):
                rates[name] = {f: float(spec.get(f, 0) or 0) for f in _RATE_FIELDS}
    return rates


def lookup(rates: dict, model: str) -> dict:
    """Find rates for model. Tries exact match, then provider/<model> suffix match."""
    if not model:
        return {}
    if model in rates:
        return rates[model]
    for key, spec in rates.items():
        if "/" in key and key.split("/", 1)[1] == model:
            return spec
    return {}


def cost_for(rates: dict, model: str, input_tokens: int, cached: int, output: int, cache_creation: int = 0) -> float:
    spec = lookup(rates, model)
    if not spec:
        return 0.0
    return (
        input_tokens * spec.get("input", 0)
        + cached * spec.get("cached", 0)
        + output * spec.get("output", 0)
        + cache_creation * spec.get("cache_creation", 0)
    ) / 1_000_000
