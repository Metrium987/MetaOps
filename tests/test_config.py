"""Tests for metaops.config module."""

import os
import pytest
from metaops.config import ModelConfig, _is_reasoning_model


class TestIsReasoningModel:
    def test_o3(self):
        assert _is_reasoning_model("o3") is True

    def test_o3_mini(self):
        assert _is_reasoning_model("o3-mini") is True

    def test_o4_mini(self):
        assert _is_reasoning_model("o4-mini") is True

    def test_gpt5(self):
        assert _is_reasoning_model("gpt-5") is True

    def test_gpt4o(self):
        assert _is_reasoning_model("gpt-4o") is False

    def test_claude(self):
        assert _is_reasoning_model("claude-sonnet-4-20250514") is False

    def test_empty(self):
        assert _is_reasoning_model("") is False


class TestModelPrefix:
    def test_bare_model_gets_prefix(self, monkeypatch):
        """Models without provider prefix get openai/ prefix."""
        monkeypatch.setenv("METAOPS_COORDINATOR_MODEL", "deepseek-v4-flash-free")
        monkeypatch.setenv("METAOPS_COORDINATOR_PROVIDER", "opencode")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = ModelConfig(
            "METAOPS_COORDINATOR_MODEL",
            "METAOPS_COORDINATOR_PROVIDER",
            "deepseek-v4-flash-free",
            "opencode",
        )
        # Verify the model string doesn't have a slash initially
        assert "/" not in config.model

    def test_model_with_prefix_kept(self, monkeypatch):
        """Models with provider prefix keep it unchanged."""
        monkeypatch.setenv("METAOPS_COORDINATOR_MODEL", "openai/gpt-4o-mini")
        monkeypatch.setenv("METAOPS_COORDINATOR_PROVIDER", "openrouter")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = ModelConfig(
            "METAOPS_COORDINATOR_MODEL",
            "METAOPS_COORDINATOR_PROVIDER",
            "openai/gpt-4o-mini",
            "openrouter",
        )
        assert config.model == "openai/gpt-4o-mini"

    def test_stepfun_model_kept(self, monkeypatch):
        """Models like stepfun/step-3.7-flash keep their prefix."""
        monkeypatch.setenv("METAOPS_COORDINATOR_MODEL", "stepfun/step-3.7-flash")
        monkeypatch.setenv("METAOPS_COORDINATOR_PROVIDER", "kilocode")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = ModelConfig(
            "METAOPS_COORDINATOR_MODEL",
            "METAOPS_COORDINATOR_PROVIDER",
            "stepfun/step-3.7-flash",
            "kilocode",
        )
        assert config.model == "stepfun/step-3.7-flash"


class TestModelConfig:
    def test_default_max_tokens_anthropic(self, monkeypatch):
        monkeypatch.delenv("METAOPS_COORDINATOR_MAX_TOKENS", raising=False)
        monkeypatch.setenv("METAOPS_COORDINATOR_MODEL", "claude-sonnet-4-20250514")
        monkeypatch.setenv("METAOPS_COORDINATOR_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        config = ModelConfig(
            "METAOPS_COORDINATOR_MODEL",
            "METAOPS_COORDINATOR_PROVIDER",
            "claude-sonnet-4-20250514",
            "anthropic",
        )
        assert config.max_tokens == 8192

    def test_default_max_tokens_openai(self, monkeypatch):
        monkeypatch.delenv("METAOPS_COORDINATOR_MAX_TOKENS", raising=False)
        monkeypatch.setenv("METAOPS_COORDINATOR_MODEL", "gpt-4o")
        monkeypatch.setenv("METAOPS_COORDINATOR_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = ModelConfig(
            "METAOPS_COORDINATOR_MODEL",
            "METAOPS_COORDINATOR_PROVIDER",
            "gpt-4o",
            "openai",
        )
        assert config.max_tokens == 16000

    def test_custom_max_tokens(self, monkeypatch):
        monkeypatch.setenv("METAOPS_COORDINATOR_MAX_TOKENS", "32000")
        monkeypatch.setenv("METAOPS_COORDINATOR_MODEL", "gpt-4o")
        monkeypatch.setenv("METAOPS_COORDINATOR_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        config = ModelConfig(
            "METAOPS_COORDINATOR_MODEL",
            "METAOPS_COORDINATOR_PROVIDER",
            "gpt-4o",
            "openai",
        )
        assert config.max_tokens == 32000
