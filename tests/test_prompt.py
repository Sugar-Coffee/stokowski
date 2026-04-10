"""Tests for prompt assembly and template rendering."""

from pathlib import Path

import pytest

from stokowski.models import Issue
from stokowski.prompt import (
    build_lifecycle_section,
    build_template_context,
    load_prompt_file,
    render_template,
)
from stokowski.config import LinearStatesConfig, StateConfig


class TestRenderTemplate:
    def test_simple_variable(self):
        result = render_template("Hello {{ name }}", {"name": "World"})
        assert result == "Hello World"

    def test_multiple_variables(self):
        result = render_template(
            "{{ a }} + {{ b }} = {{ c }}",
            {"a": "1", "b": "2", "c": "3"},
        )
        assert result == "1 + 2 = 3"

    def test_missing_variable_silent(self):
        result = render_template("Value: {{ missing }}", {})
        assert result == "Value: "

    def test_nested_missing_silent(self):
        result = render_template("Got: {{ obj.attr }}", {})
        assert "obj" not in result  # silent undefined renders empty

    def test_conditionals_with_missing(self):
        result = render_template(
            "{% if thing %}yes{% else %}no{% endif %}",
            {},
        )
        assert result == "no"

    def test_loop(self):
        result = render_template(
            "{% for i in items %}{{ i }} {% endfor %}",
            {"items": ["a", "b", "c"]},
        )
        assert result.strip() == "a b c"

    def test_no_template_syntax(self):
        result = render_template("Plain text with no vars", {})
        assert result == "Plain text with no vars"

    def test_integer_variable(self):
        result = render_template("Run #{{ run }}", {"run": 3})
        assert result == "Run #3"


class TestBuildTemplateContext:
    def _make_issue(self, **kwargs):
        defaults = dict(
            id="abc",
            identifier="DEV-1",
            title="Fix the thing",
            description="Detailed desc",
            priority=2,
            state="In Progress",
            branch_name="feature/dev-1",
            url="https://linear.app/DEV-1",
            labels=["bug", "p1"],
        )
        defaults.update(kwargs)
        return Issue(**defaults)

    def test_all_keys_present(self):
        issue = self._make_issue()
        ctx = build_template_context(issue, "implement", run=2, attempt=1)
        expected_keys = {
            "issue_id", "issue_identifier", "issue_title",
            "issue_description", "issue_url", "issue_priority",
            "issue_state", "issue_branch", "issue_labels",
            "state_name", "run", "attempt", "last_run_at",
        }
        assert set(ctx.keys()) == expected_keys

    def test_values_correct(self):
        issue = self._make_issue()
        ctx = build_template_context(issue, "implement", run=2, attempt=3)
        assert ctx["issue_id"] == "abc"
        assert ctx["issue_identifier"] == "DEV-1"
        assert ctx["issue_title"] == "Fix the thing"
        assert ctx["issue_description"] == "Detailed desc"
        assert ctx["issue_url"] == "https://linear.app/DEV-1"
        assert ctx["issue_priority"] == 2
        assert ctx["issue_state"] == "In Progress"
        assert ctx["issue_branch"] == "feature/dev-1"
        assert ctx["state_name"] == "implement"
        assert ctx["run"] == 2
        assert ctx["attempt"] == 3

    def test_labels_joined(self):
        issue = self._make_issue(labels=["feat", "docs"])
        ctx = build_template_context(issue, "work")
        assert ctx["issue_labels"] == "feat, docs"

    def test_none_fields_default(self):
        issue = Issue(id="x", identifier="X-1", title="T")
        ctx = build_template_context(issue, "work")
        assert ctx["issue_description"] == ""
        assert ctx["issue_url"] == ""
        assert ctx["issue_branch"] == ""
        assert ctx["last_run_at"] == ""

    def test_last_run_at_passed_through(self):
        issue = self._make_issue()
        ctx = build_template_context(
            issue, "work", last_run_at="2026-01-01T00:00:00Z"
        )
        assert ctx["last_run_at"] == "2026-01-01T00:00:00Z"


class TestLoadPromptFile:
    def test_loads_relative_path(self, tmp_path):
        prompt_file = tmp_path / "prompts" / "impl.md"
        prompt_file.parent.mkdir()
        prompt_file.write_text("Do the work for {{ issue_identifier }}")
        result = load_prompt_file("prompts/impl.md", tmp_path)
        assert "Do the work" in result

    def test_loads_absolute_path(self, tmp_path):
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Absolute prompt")
        result = load_prompt_file(str(prompt_file), tmp_path)
        assert result == "Absolute prompt"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_prompt_file("nonexistent.md", tmp_path)


class TestBuildLifecycleSection:
    def _make_state_cfg(self, **kwargs):
        defaults = dict(
            name="implement",
            type="agent",
            prompt="p.md",
            linear_state="active",
            transitions={"complete": "done"},
        )
        defaults.update(kwargs)
        return StateConfig(**defaults)

    def test_contains_issue_metadata(self):
        issue = Issue(id="1", identifier="DEV-1", title="Test", url="http://url")
        section = build_lifecycle_section(
            issue=issue,
            state_name="implement",
            state_cfg=self._make_state_cfg(),
            linear_states=LinearStatesConfig(),
        )
        assert "DEV-1" in section
        assert "Test" in section
        assert "http://url" in section
        assert "implement" in section

    def test_contains_transitions(self):
        section = build_lifecycle_section(
            issue=Issue(id="1", identifier="X", title="T"),
            state_name="work",
            state_cfg=self._make_state_cfg(transitions={"complete": "review", "skip": "done"}),
            linear_states=LinearStatesConfig(),
        )
        assert "complete" in section
        assert "review" in section
        assert "skip" in section
        assert "done" in section

    def test_rework_section(self):
        comments = [{"body": "Please fix the tests", "createdAt": "2026-01-01"}]
        section = build_lifecycle_section(
            issue=Issue(id="1", identifier="X", title="T"),
            state_name="implement",
            state_cfg=self._make_state_cfg(),
            linear_states=LinearStatesConfig(),
            is_rework=True,
            recent_comments=comments,
        )
        assert "rework" in section.lower()
        assert "fix the tests" in section

    def test_no_transitions(self):
        section = build_lifecycle_section(
            issue=Issue(id="1", identifier="X", title="T"),
            state_name="done",
            state_cfg=self._make_state_cfg(transitions=None),
            linear_states=LinearStatesConfig(),
        )
        assert "Transitions" not in section

    def test_auto_generated_markers(self):
        section = build_lifecycle_section(
            issue=Issue(id="1", identifier="X", title="T"),
            state_name="work",
            state_cfg=self._make_state_cfg(),
            linear_states=LinearStatesConfig(),
        )
        assert "AUTO-GENERATED BY STOKOWSKI" in section
        assert "END STOKOWSKI LIFECYCLE" in section
