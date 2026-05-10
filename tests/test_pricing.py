import json
import time
from unittest.mock import patch

import pytest

import pricing


@pytest.fixture
def litellm_sample():
    return {
        "sample_spec": {"input_cost_per_token": 1, "output_cost_per_token": 1},
        "gpt-5": {
            "input_cost_per_token": 1.25e-6,
            "output_cost_per_token": 1.0e-5,
            "cache_read_input_token_cost": 1.25e-7,
            "cache_creation_input_token_cost": 1.25e-6,
        },
        "deepseek/deepseek-chat": {
            "input_cost_per_token": 2.7e-7,
            "output_cost_per_token": 1.1e-6,
        },
        "no-cost-model": {"litellm_provider": "x"},
    }


class TestNormalizeLitellm:
    def test_per_mtok_conversion(self, litellm_sample):
        out = pricing._normalize_litellm(litellm_sample)
        assert out["gpt-5"] == {
            "input": pytest.approx(1.25),
            "output": pytest.approx(10.0),
            "cached": pytest.approx(0.125),
            "cache_creation": pytest.approx(1.25),
        }

    def test_skips_entries_without_costs(self, litellm_sample):
        out = pricing._normalize_litellm(litellm_sample)
        assert "no-cost-model" not in out
        assert "sample_spec" in out  # sample_spec has costs and is included

    def test_default_cached_to_input_rate(self, litellm_sample):
        out = pricing._normalize_litellm(litellm_sample)
        # deepseek/deepseek-chat has no cache_read_input_token_cost
        assert out["deepseek/deepseek-chat"]["cached"] == pytest.approx(0.27)


class TestLookup:
    def test_exact_match(self):
        rates = {"gpt-5": {"input": 1.25}}
        assert pricing.lookup(rates, "gpt-5") == {"input": 1.25}

    def test_provider_prefix_fallback(self):
        rates = {"deepseek/deepseek-chat": {"input": 0.27}}
        assert pricing.lookup(rates, "deepseek-chat") == {"input": 0.27}

    def test_unknown_model_returns_empty(self):
        assert pricing.lookup({"gpt-5": {"input": 1}}, "mystery-model") == {}

    def test_empty_model_name(self):
        assert pricing.lookup({"gpt-5": {"input": 1}}, "") == {}


class TestCostFor:
    def test_known_model(self):
        rates = {"gpt-5": {"input": 1.25, "cached": 0.125, "output": 10.0, "cache_creation": 1.25}}
        # 1M input + 100K output -> 1.25 + 1.0 = 2.25
        cost = pricing.cost_for(rates, "gpt-5", 1_000_000, 0, 100_000)
        assert cost == pytest.approx(2.25)

    def test_unknown_model_is_zero(self):
        assert pricing.cost_for({}, "mystery", 1_000_000, 0, 100_000) == 0.0

    def test_includes_cached_and_cache_creation(self):
        rates = {"m": {"input": 1.0, "cached": 0.1, "output": 2.0, "cache_creation": 0.5}}
        # 1M input * 1 + 1M cached * 0.1 + 1M output * 2 + 1M cc * 0.5 = 3.6
        cost = pricing.cost_for(rates, "m", 1_000_000, 1_000_000, 1_000_000, 1_000_000)
        assert cost == pytest.approx(3.6)


class TestLoadPricing:
    def test_override_wins_over_litellm(self, tmp_path, litellm_sample):
        cache = tmp_path / "litellm.json"
        cache.write_text(json.dumps(litellm_sample))
        override = tmp_path / "pricing.json"
        override.write_text(json.dumps({"models": {"gpt-5": {"input": 99, "output": 99, "cached": 99, "cache_creation": 99}}}))

        # Fresh cache so no fetch attempt
        with patch.object(pricing, "_fetch_litellm", return_value=None):
            rates = pricing.load_pricing(str(override), str(cache), ttl=3600)
        assert rates["gpt-5"]["input"] == 99
        assert rates["deepseek/deepseek-chat"]["input"] == pytest.approx(0.27)

    def test_missing_override_is_ok(self, tmp_path, litellm_sample):
        cache = tmp_path / "litellm.json"
        cache.write_text(json.dumps(litellm_sample))
        with patch.object(pricing, "_fetch_litellm", return_value=None):
            rates = pricing.load_pricing("", str(cache), ttl=3600)
        assert "gpt-5" in rates

    def test_stale_cache_triggers_fetch(self, tmp_path, litellm_sample):
        cache = tmp_path / "litellm.json"
        cache.write_text("{}")
        old = time.time() - 7200
        import os
        os.utime(cache, (old, old))
        with patch.object(pricing, "_fetch_litellm", return_value=litellm_sample) as m:
            rates = pricing.load_pricing("", str(cache), ttl=3600)
        m.assert_called_once()
        assert "gpt-5" in rates

    def test_fetch_failure_falls_back_to_cache(self, tmp_path, litellm_sample):
        cache = tmp_path / "litellm.json"
        cache.write_text(json.dumps(litellm_sample))
        old = time.time() - 7200
        import os
        os.utime(cache, (old, old))
        with patch.object(pricing, "_fetch_litellm", return_value=None):
            rates = pricing.load_pricing("", str(cache), ttl=3600)
        assert "gpt-5" in rates

    def test_missing_cache_and_no_fetch(self, tmp_path):
        cache = tmp_path / "litellm.json"
        with patch.object(pricing, "_fetch_litellm", return_value=None):
            rates = pricing.load_pricing("", str(cache), ttl=3600)
        assert rates == {}
