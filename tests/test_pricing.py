"""Tests for the LLM price table and cost computation.

Covers issue #60's pricing acceptance criteria:
  - Known-model cost is computed correctly across all four token buckets.
  - Unknown models return 0.0 (don't crash) and log a warning exactly once.
  - Missing keys in the usage dict default to 0 — providers shouldn't have
    to fill in zero-valued buckets.
"""
import logging

import pytest

from providers.llm import pricing
from providers.llm.pricing import PRICES_PER_MTOKEN, compute_cost


@pytest.fixture(autouse=True)
def _reset_warning_state():
    """Each test starts with a clean ``_WARNED_UNKNOWN`` set."""
    pricing._WARNED_UNKNOWN.clear()


class TestComputeCost:
    def test_sonnet_input_only(self):
        # 1M input tokens @ $3.0/MT → $3.00
        cost = compute_cost("claude-sonnet-4-6", {"input_tokens": 1_000_000})
        assert cost == pytest.approx(3.0)

    def test_sonnet_input_and_output(self):
        # 1000 in @ $3/MT + 500 out @ $15/MT = 0.003 + 0.0075 = 0.0105
        cost = compute_cost(
            "claude-sonnet-4-6",
            {"input_tokens": 1000, "output_tokens": 500},
        )
        assert cost == pytest.approx(0.0105)

    def test_sonnet_with_cache_tokens(self):
        cost = compute_cost(
            "claude-sonnet-4-6",
            {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 10_000,
                "cache_creation_input_tokens": 2000,
            },
        )
        # 0.003 + 0.0075 + 10000*0.3/1e6 + 2000*3.75/1e6
        # = 0.003 + 0.0075 + 0.003 + 0.0075 = 0.021
        assert cost == pytest.approx(0.021)

    def test_haiku_cheaper_than_sonnet(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
        haiku_cost = compute_cost("claude-haiku-4-5-20251001", usage)
        sonnet_cost = compute_cost("claude-sonnet-4-6", usage)
        assert haiku_cost < sonnet_cost

    def test_gpt4o_known(self):
        cost = compute_cost("gpt-4o", {"input_tokens": 1_000_000})
        assert cost == pytest.approx(2.5)

    def test_gpt4o_mini_known(self):
        cost = compute_cost("gpt-4o-mini", {"input_tokens": 1_000_000})
        assert cost == pytest.approx(0.15)

    def test_missing_keys_default_to_zero(self):
        # An empty usage dict should be valid → $0.00, not a KeyError.
        cost = compute_cost("claude-sonnet-4-6", {})
        assert cost == 0.0

    def test_none_values_treated_as_zero(self):
        cost = compute_cost(
            "claude-sonnet-4-6",
            {"input_tokens": None, "output_tokens": None},
        )
        assert cost == 0.0


class TestUnknownModelFallback:
    def test_unknown_model_returns_zero(self):
        cost = compute_cost("totally-fake-model", {"input_tokens": 1000})
        assert cost == 0.0

    def test_unknown_model_warns_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger="providers.llm.pricing"):
            compute_cost("never-heard-of-it", {"input_tokens": 1000})
            compute_cost("never-heard-of-it", {"input_tokens": 2000})
            compute_cost("never-heard-of-it", {"input_tokens": 3000})

        # Exactly one warning for that model name, regardless of call count.
        matching = [r for r in caplog.records if "never-heard-of-it" in r.getMessage()]
        assert len(matching) == 1

    def test_different_unknown_models_each_warn_once(self, caplog):
        with caplog.at_level(logging.WARNING, logger="providers.llm.pricing"):
            compute_cost("unknown-model-a", {})
            compute_cost("unknown-model-b", {})
            compute_cost("unknown-model-a", {})  # already warned
            compute_cost("unknown-model-b", {})  # already warned

        messages = [r.getMessage() for r in caplog.records]
        a_count = sum(1 for m in messages if "unknown-model-a" in m)
        b_count = sum(1 for m in messages if "unknown-model-b" in m)
        assert a_count == 1
        assert b_count == 1


class TestPriceTable:
    def test_required_models_present(self):
        # The acceptance criteria spell out a minimum set — guard against
        # accidental deletion in future refactors.
        required = {
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
            "gpt-4o",
            "gpt-4o-mini",
        }
        assert required <= set(PRICES_PER_MTOKEN.keys())

    def test_every_entry_has_all_four_buckets(self):
        required_keys = {"input", "output", "cache_read", "cache_create"}
        for model, rates in PRICES_PER_MTOKEN.items():
            assert required_keys <= set(rates.keys()), (
                f"price entry for {model} is missing a bucket"
            )

    def test_output_costs_more_than_input(self):
        # Sanity check: every vendor charges more for output than input.
        # If this ever flips, the price table is almost certainly wrong.
        for model, rates in PRICES_PER_MTOKEN.items():
            assert rates["output"] >= rates["input"], (
                f"{model}: output rate {rates['output']} < input {rates['input']}"
            )
