"""Tests for metaops.core.continuation module."""

import pytest
from metaops.core.continuation import (
    CONTINUE_PROMPT,
    filter_thought_parts,
    is_truncated,
    has_budget_exhausted,
    _get_max_continuations,
)
from metaops.core.reasoning_guard import REASONING_BUDGET_EXHAUSTED


class TestConstants:
    def test_max_continuations_is_positive(self):
        assert _get_max_continuations() > 0

    def test_continue_prompt_is_string(self):
        assert isinstance(CONTINUE_PROMPT, str)
        assert len(CONTINUE_PROMPT) > 0


class TestFilterThoughtParts:
    def test_filters_thought_parts(self):
        class FakePart:
            def __init__(self, text, thought=False):
                self.text = text
                self.thought = thought

        parts = [
            FakePart("Visible text", thought=False),
            FakePart("Hidden reasoning", thought=True),
            FakePart("More visible", thought=False),
        ]
        result = filter_thought_parts(parts)
        assert result == ["Visible text", "More visible"]

    def test_filters_none_text(self):
        class FakePart:
            def __init__(self, text, thought=False):
                self.text = text
                self.thought = thought

        parts = [
            FakePart(None, thought=False),
            FakePart("Visible", thought=False),
        ]
        result = filter_thought_parts(parts)
        assert result == ["Visible"]

    def test_empty_parts(self):
        assert filter_thought_parts([]) == []
        assert filter_thought_parts(None) == []


class TestIsTruncated:
    def test_truncated(self):
        class FakeEvent:
            custom_metadata = {"metaops_truncated": True}
        assert is_truncated(FakeEvent()) is True

    def test_not_truncated(self):
        class FakeEvent:
            custom_metadata = {}
        assert is_truncated(FakeEvent()) is False

    def test_no_metadata(self):
        class FakeEvent:
            custom_metadata = None
        assert is_truncated(FakeEvent()) is False


class TestHasBudgetExhausted:
    def test_exhausted(self):
        assert has_budget_exhausted(REASONING_BUDGET_EXHAUSTED) is True

    def test_not_exhausted(self):
        assert has_budget_exhausted(None) is False
        assert has_budget_exhausted("OTHER_ERROR") is False
        assert has_budget_exhausted("") is False
