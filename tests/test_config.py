"""Tests for config parsing and validation."""

import textwrap
from pathlib import Path

import pytest

from stokowski.config import (
    GitHubStatesConfig,
    LinearStatesConfig,
    ServiceConfig,
    StateConfig,
    TrackerConfig,
    parse_workflow_file,
    validate_config,
)


@pytest.fixture
def tmp_yaml(tmp_path):
    """Helper to write a YAML file and return its path."""

    def _write(content: str, name: str = "workflow.yaml") -> Path:
        p = tmp_path / name
        p.write_text(textwrap.dedent(content))
        return p

    return _write


class TestParseWorkflow:
    def test_minimal_yaml(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              api_key: test_key
              team_key: DEV
            states:
              implement:
                type: agent
                prompt: prompts/impl.md
                linear_state: active
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.tracker.kind == "linear"
        assert wf.config.tracker.api_key == "test_key"
        assert "implement" in wf.config.states
        assert "done" in wf.config.states

    def test_github_tracker_fields(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: github
              github_owner: my-org
              github_repo: my-repo
              github_token: ghp_test123
            states:
              work:
                type: agent
                prompt: prompts/work.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.tracker.kind == "github"
        assert wf.config.tracker.github_owner == "my-org"
        assert wf.config.tracker.github_repo == "my-repo"
        assert wf.config.tracker.github_token == "ghp_test123"

    def test_schedule_parsing(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            schedule:
              cron: "0 9 * * *"
              create_command: "gh issue create --title test"
            states:
              work:
                type: agent
                prompt: prompts/work.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.schedule is not None
        assert wf.config.schedule.cron == "0 9 * * *"
        assert "gh issue create" in wf.config.schedule.create_command

    def test_webhook_secret_parsing(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            webhook:
              secret: my_secret
            states:
              work:
                type: agent
                prompt: prompts/work.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.webhook.secret == "my_secret"

    def test_github_states_parsing(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: github
              github_owner: org
              github_repo: repo
            github_states:
              todo: "ready"
              active: "working"
              terminal:
                - "shipped"
              close_on_terminal: false
            states:
              work:
                type: agent
                prompt: prompts/work.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.github_states.todo == "ready"
        assert wf.config.github_states.active == "working"
        assert wf.config.github_states.terminal == ["shipped"]
        assert wf.config.github_states.close_on_terminal is False

    def test_auto_approve_parsed(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              api_key: test_key
              team_key: DEV
            states:
              implement:
                type: agent
                prompt: prompts/impl.md
                linear_state: active
                transitions:
                  complete: review
              review:
                type: gate
                linear_state: review
                rework_to: implement
                auto_approve: when_no_questions
                transitions:
                  approve: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.states["review"].auto_approve == "when_no_questions"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_workflow_file("/nonexistent/path.yaml")

    def test_md_format_with_frontmatter(self, tmp_yaml):
        path = tmp_yaml(
            """---
tracker:
  kind: linear
  team_key: DEV
states:
  work:
    type: agent
    prompt: prompts/work.md
    transitions:
      complete: done
  done:
    type: terminal
    linear_state: terminal
---
This is the prompt template body.
""",
            name="workflow.md",
        )
        wf = parse_workflow_file(path)
        assert wf.config.tracker.kind == "linear"
        assert "prompt template body" in wf.prompt_template


class TestValidateConfig:
    def _make_cfg(self, **overrides):
        states = overrides.pop("states", {
            "work": StateConfig(
                name="work",
                type="agent",
                prompt="prompts/work.md",
                linear_state="active",
                transitions={"complete": "done"},
            ),
            "done": StateConfig(
                name="done",
                type="terminal",
                linear_state="terminal",
            ),
        })
        defaults = dict(
            tracker=TrackerConfig(kind="linear", api_key="test", team_key="DEV"),
            states=states,
        )
        defaults.update(overrides)
        return ServiceConfig(**defaults)

    def test_valid_config_no_errors(self):
        cfg = self._make_cfg()
        assert validate_config(cfg) == []

    def test_missing_api_key(self):
        cfg = self._make_cfg(tracker=TrackerConfig(kind="linear", team_key="DEV"))
        errors = validate_config(cfg)
        assert any("API key" in e for e in errors)

    def test_missing_github_owner_repo(self):
        cfg = self._make_cfg(
            tracker=TrackerConfig(kind="github", github_token="tok")
        )
        errors = validate_config(cfg)
        assert any("github_owner" in e for e in errors)

    def test_unsupported_tracker_kind(self):
        cfg = self._make_cfg(
            tracker=TrackerConfig(kind="jira", api_key="x", team_key="T")
        )
        errors = validate_config(cfg)
        assert any("Unsupported" in e for e in errors)

    def test_no_states(self):
        cfg = self._make_cfg(states={})
        errors = validate_config(cfg)
        assert any("No states defined" in e for e in errors)

    def test_missing_agent_prompt(self):
        cfg = self._make_cfg(states={
            "work": StateConfig(
                name="work", type="agent", linear_state="active",
                transitions={"complete": "done"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert any("missing 'prompt'" in e for e in errors)

    def test_gate_missing_rework_to(self):
        cfg = self._make_cfg(states={
            "work": StateConfig(
                name="work", type="agent", prompt="p.md",
                linear_state="active", transitions={"complete": "review"},
            ),
            "review": StateConfig(
                name="review", type="gate", linear_state="review",
                transitions={"approve": "done"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert any("rework_to" in e for e in errors)

    def test_transition_to_unknown_state(self):
        cfg = self._make_cfg(states={
            "work": StateConfig(
                name="work", type="agent", prompt="p.md",
                linear_state="active", transitions={"complete": "nonexistent"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert any("nonexistent" in e for e in errors)

    def test_todo_is_valid_linear_state(self):
        cfg = self._make_cfg(states={
            "review": StateConfig(
                name="review", type="agent", prompt="p.md",
                linear_state="todo", transitions={"complete": "done"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert not any("invalid linear_state" in e for e in errors)

    def test_blocked_is_valid_linear_state(self):
        cfg = self._make_cfg(states={
            "work": StateConfig(
                name="work", type="agent", prompt="p.md",
                linear_state="blocked", transitions={"complete": "done"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert not any("invalid linear_state" in e for e in errors)

    def test_gate_auto_approve_defaults_to_never(self):
        cfg = self._make_cfg(states={
            "work": StateConfig(
                name="work", type="agent", prompt="p.md",
                linear_state="active", transitions={"complete": "review"},
            ),
            "review": StateConfig(
                name="review", type="gate", linear_state="review",
                rework_to="work", transitions={"approve": "done"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert not any("auto_approve" in e for e in errors)
        assert cfg.states["review"].auto_approve == "never"

    def test_gate_auto_approve_valid_values(self):
        for value in ("never", "when_no_questions", "always"):
            cfg = self._make_cfg(states={
                "work": StateConfig(
                    name="work", type="agent", prompt="p.md",
                    linear_state="active", transitions={"complete": "review"},
                ),
                "review": StateConfig(
                    name="review", type="gate", linear_state="review",
                    rework_to="work", auto_approve=value,
                    transitions={"approve": "done"},
                ),
                "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
            })
            errors = validate_config(cfg)
            assert not any("auto_approve" in e for e in errors)

    def test_gate_auto_approve_invalid_value(self):
        cfg = self._make_cfg(states={
            "work": StateConfig(
                name="work", type="agent", prompt="p.md",
                linear_state="active", transitions={"complete": "review"},
            ),
            "review": StateConfig(
                name="review", type="gate", linear_state="review",
                rework_to="work", auto_approve="invalid",
                transitions={"approve": "done"},
            ),
            "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
        })
        errors = validate_config(cfg)
        assert any("auto_approve" in e for e in errors)


class TestActiveStates:
    def test_linear_active_states(self):
        cfg = ServiceConfig(
            tracker=TrackerConfig(kind="linear"),
            states={
                "work": StateConfig(name="work", type="agent", linear_state="active"),
                "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
            },
        )
        active = cfg.active_linear_states()
        assert "Todo" in active
        assert "In Progress" in active

    def test_github_active_states(self):
        cfg = ServiceConfig(
            tracker=TrackerConfig(kind="github"),
            github_states=GitHubStatesConfig(todo="ready", active="working"),
            states={
                "work": StateConfig(name="work", type="agent", linear_state="active"),
                "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
            },
        )
        active = cfg.active_linear_states()
        assert "ready" in active
        assert "working" in active

    def test_terminal_states(self):
        cfg = ServiceConfig(
            linear_states=LinearStatesConfig(terminal=["Done", "Cancelled"]),
            states={
                "done": StateConfig(name="done", type="terminal", linear_state="terminal"),
            },
        )
        assert cfg.terminal_linear_states() == ["Done", "Cancelled"]


class TestPerWorkflowFiltering:
    def test_parse_filter_labels(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            filter_labels: [work, bug]
            pickup_states: [Todo, Triage]
            states:
              work:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.filter_labels == ["work", "bug"]
        assert wf.config.pickup_states == ["Todo", "Triage"]

    def test_tracker_enabled_default_true(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            states:
              work:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.tracker_enabled is True

    def test_tracker_enabled_false(self, tmp_yaml):
        path = tmp_yaml("""
            tracker_enabled: false
            states:
              sync:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.tracker_enabled is False

    def test_workspace_enabled_false(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            workspace_enabled: false
            states:
              work:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.workspace_enabled is False

    def test_tracker_disabled_skips_validation(self, tmp_yaml):
        path = tmp_yaml("""
            tracker_enabled: false
            states:
              work:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        errors = validate_config(wf.config)
        # No tracker errors when tracker is disabled
        assert not any("API key" in e for e in errors)
        assert not any("project_slug" in e for e in errors)

    def test_terminal_linear_state_none(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            states:
              work:
                type: agent
                prompt: p.md
                linear_state: active
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: none
        """)
        wf = parse_workflow_file(path)
        assert wf.config.states["done"].linear_state == "none"
        errors = validate_config(wf.config)
        assert not any("invalid linear_state" in e for e in errors)

    def test_terminal_literal_state_name(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            states:
              work:
                type: agent
                prompt: p.md
                linear_state: active
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: Todo
        """)
        wf = parse_workflow_file(path)
        assert wf.config.states["done"].linear_state == "Todo"
        errors = validate_config(wf.config)
        # Capitalized literal names should not be rejected
        assert not any("invalid linear_state" in e for e in errors)

    def test_fallback_runners(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: linear
              team_key: DEV
            states:
              work:
                type: agent
                prompt: p.md
                linear_state: active
                fallback_runners: [gemini, codex]
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        wf = parse_workflow_file(path)
        assert wf.config.states["work"].fallback_runners == ["gemini", "codex"]


class TestSharedConfig:
    def test_shared_raw_merges_into_workflow(self, tmp_yaml):
        path = tmp_yaml("""
            polling:
              interval_ms: 5000
            states:
              work:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        shared = {
            "tracker": {"kind": "linear", "team_key": "DEV", "api_key": "test"},
            "claude": {"model": "claude-sonnet-4-6"},
        }
        wf = parse_workflow_file(path, shared_raw=shared)
        assert wf.config.tracker.kind == "linear"
        assert wf.config.tracker.team_key == "DEV"
        assert wf.config.claude.model == "claude-sonnet-4-6"
        assert wf.config.polling.interval_ms == 5000

    def test_workflow_overrides_shared(self, tmp_yaml):
        path = tmp_yaml("""
            tracker:
              kind: github
              github_owner: org
              github_repo: repo
            states:
              work:
                type: agent
                prompt: p.md
                transitions:
                  complete: done
              done:
                type: terminal
                linear_state: terminal
        """)
        shared = {"tracker": {"kind": "linear", "team_key": "DEV"}}
        wf = parse_workflow_file(path, shared_raw=shared)
        assert wf.config.tracker.kind == "github"
