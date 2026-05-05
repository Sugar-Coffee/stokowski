"""Workflow loader and typed configuration."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


@dataclass
class TrackerConfig:
    kind: str = "linear"
    endpoint: str = "https://api.linear.app/graphql"
    api_key: str = ""
    project_slug: str = ""


@dataclass
class PollingConfig:
    interval_ms: int = 30_000


@dataclass
class WorkspaceConfig:
    root: str = ""

    def resolved_root(self) -> Path:
        if self.root:
            return Path(os.path.expandvars(os.path.expanduser(self.root)))
        return Path(tempfile.gettempdir()) / "stokowski_workspaces"


@dataclass
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    on_stage_enter: str | None = None
    timeout_ms: int = 60_000


@dataclass
class ClaudeConfig:
    command: str = "claude"
    permission_mode: str = "auto"  # "auto" or "allowedTools"
    allowed_tools: list[str] = field(
        default_factory=lambda: ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
    )
    model: str | None = None
    max_turns: int = 20
    turn_timeout_ms: int = 3_600_000
    stall_timeout_ms: int = 300_000
    append_system_prompt: str | None = None


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 5
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)
    # Optional per-project cap. Keys are project names; values cap how many
    # of the global pool a project may hold at once. A project may also set
    # `max_concurrent` in its own block — that takes precedence over this map.
    max_concurrent_per_project: dict[str, int] = field(default_factory=dict)


@dataclass
class ServerConfig:
    port: int | None = None


@dataclass
class LinearStatesConfig:
    """Maps logical state names to actual Linear state names."""
    todo: str = "Todo"
    active: str = "In Progress"
    awaiting_ci: str = "Awaiting CI"
    review: str = "Human Review"
    gate_approved: str = "Gate Approved"
    rework: str = "Rework"
    terminal: list[str] = field(default_factory=lambda: ["Done", "Closed", "Cancelled"])


@dataclass
class PromptsConfig:
    """Prompt file references."""
    global_prompt: str | None = None


@dataclass
class StateConfig:
    """A single state in the state machine."""
    name: str = ""
    type: str = "agent"              # "agent", "gate", "terminal"
    prompt: str | None = None        # path to prompt .md file
    linear_state: str = "active"     # key into LinearStatesConfig
    runner: str = "claude"
    model: str | None = None
    max_turns: int | None = None
    turn_timeout_ms: int | None = None
    stall_timeout_ms: int | None = None
    session: str = "inherit"
    permission_mode: str | None = None
    allowed_tools: list[str] | None = None
    rework_to: str | None = None     # gate only
    max_rework: int | None = None    # gate only
    transitions: dict[str, str] = field(default_factory=dict)
    hooks: HooksConfig | None = None


@dataclass
class ProjectConfig:
    """A single project's resolved config — flattens global defaults + per-project overrides.

    Each project gets its own Linear tracker, workspace, hooks, prompts,
    state machine, and (optionally) Linear-state and Claude overrides.
    Multi-project setups use one ProjectConfig per `projects:` entry;
    legacy single-project setups synthesize one ProjectConfig from the
    top-level fields.
    """
    name: str = ""
    paused: bool = False
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    states: dict[str, StateConfig] = field(default_factory=dict)
    linear_states: LinearStatesConfig = field(default_factory=LinearStatesConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    workflow_dir: Path = field(default_factory=lambda: Path("."))
    # Per-project cap (overrides AgentConfig.max_concurrent_per_project[name]).
    max_concurrent: int | None = None

    def resolved_api_key(self) -> str:
        key = self.tracker.api_key
        if not key:
            return os.environ.get("LINEAR_API_KEY", "")
        if key.startswith("$"):
            return os.environ.get(key[1:], "")
        return key

    def agent_env(self) -> dict[str, str]:
        """Build env vars to pass to agent subprocesses for this project."""
        env = dict(os.environ)
        api_key = self.resolved_api_key()
        if api_key:
            env["LINEAR_API_KEY"] = api_key
        if self.tracker.project_slug:
            env["LINEAR_PROJECT_SLUG"] = self.tracker.project_slug
        if self.tracker.endpoint:
            env["LINEAR_ENDPOINT"] = self.tracker.endpoint
        env["STOKOWSKI_PROJECT"] = self.name
        return env

    @property
    def entry_state(self) -> str | None:
        for name, sc in self.states.items():
            if sc.type == "agent":
                return name
        return None

    def active_linear_states(self) -> list[str]:
        ls = self.linear_states
        seen: list[str] = []
        if ls.todo and ls.todo not in seen:
            seen.append(ls.todo)
        for sc in self.states.values():
            if sc.type == "agent":
                linear_name = _resolve_linear_state_name(sc.linear_state, ls)
                if linear_name and linear_name not in seen:
                    seen.append(linear_name)
        return seen

    def gate_linear_states(self) -> list[str]:
        ls = self.linear_states
        seen: list[str] = []
        for sc in self.states.values():
            if sc.type == "gate":
                linear_name = _resolve_linear_state_name(sc.linear_state, ls)
                if linear_name and linear_name not in seen:
                    seen.append(linear_name)
        return seen

    def terminal_linear_states(self) -> list[str]:
        return list(self.linear_states.terminal)


@dataclass
class WorkflowDefinition:
    config: ServiceConfig
    prompt_template: str


@dataclass
class ServiceConfig:
    """Top-level config. `projects` is the authoritative project list.

    Top-level `tracker`, `workspace`, `hooks`, `prompts`, `states`,
    `linear_states`, `claude` remain populated for backward compat with
    single-project workflows — they mirror `projects[0]` in that case.
    """
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    linear_states: LinearStatesConfig = field(default_factory=LinearStatesConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    states: dict[str, StateConfig] = field(default_factory=dict)
    projects: list[ProjectConfig] = field(default_factory=list)
    workflow_dir: Path = field(default_factory=lambda: Path("."))

    def resolved_api_key(self) -> str:
        # Legacy passthrough — delegates to first project.
        if self.projects:
            return self.projects[0].resolved_api_key()
        key = self.tracker.api_key
        if not key:
            return os.environ.get("LINEAR_API_KEY", "")
        if key.startswith("$"):
            return os.environ.get(key[1:], "")
        return key

    def agent_env(self) -> dict[str, str]:
        if self.projects:
            return self.projects[0].agent_env()
        env = dict(os.environ)
        if self.tracker.api_key:
            env["LINEAR_API_KEY"] = self.resolved_api_key()
        if self.tracker.project_slug:
            env["LINEAR_PROJECT_SLUG"] = self.tracker.project_slug
        if self.tracker.endpoint:
            env["LINEAR_ENDPOINT"] = self.tracker.endpoint
        return env

    @property
    def entry_state(self) -> str | None:
        if self.projects:
            return self.projects[0].entry_state
        for name, sc in self.states.items():
            if sc.type == "agent":
                return name
        return None

    def active_linear_states(self) -> list[str]:
        if self.projects:
            return self.projects[0].active_linear_states()
        ls = self.linear_states
        seen: list[str] = []
        if ls.todo and ls.todo not in seen:
            seen.append(ls.todo)
        for sc in self.states.values():
            if sc.type == "agent":
                linear_name = _resolve_linear_state_name(sc.linear_state, ls)
                if linear_name and linear_name not in seen:
                    seen.append(linear_name)
        return seen

    def gate_linear_states(self) -> list[str]:
        if self.projects:
            return self.projects[0].gate_linear_states()
        ls = self.linear_states
        seen: list[str] = []
        for sc in self.states.values():
            if sc.type == "gate":
                linear_name = _resolve_linear_state_name(sc.linear_state, ls)
                if linear_name and linear_name not in seen:
                    seen.append(linear_name)
        return seen

    def terminal_linear_states(self) -> list[str]:
        if self.projects:
            return self.projects[0].terminal_linear_states()
        return list(self.linear_states.terminal)


def _resolve_linear_state_name(key: str, ls: LinearStatesConfig) -> str:
    """Resolve a logical state key to the actual Linear state name."""
    mapping: dict[str, str] = {
        "active": ls.active,
        "awaiting_ci": ls.awaiting_ci,
        "review": ls.review,
        "gate_approved": ls.gate_approved,
        "rework": ls.rework,
    }
    return mapping.get(key, key)


def _resolve_env(val: str) -> str:
    if isinstance(val, str) and val.startswith("$"):
        return os.environ.get(val[1:], "")
    return val


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _coerce_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [s.strip() for s in val.split(",") if s.strip()]
    return []


def _parse_hooks(raw: dict[str, Any] | None) -> HooksConfig | None:
    """Parse a hooks dict into HooksConfig, returning None if empty."""
    if not raw:
        return None
    return HooksConfig(
        after_create=raw.get("after_create"),
        before_run=raw.get("before_run"),
        after_run=raw.get("after_run"),
        before_remove=raw.get("before_remove"),
        on_stage_enter=raw.get("on_stage_enter"),
        timeout_ms=_coerce_int(raw.get("timeout_ms"), 60_000),
    )


def _parse_state_config(name: str, raw: dict[str, Any]) -> StateConfig:
    """Parse a single state entry from YAML into StateConfig."""
    allowed = raw.get("allowed_tools")
    hooks_raw = raw.get("hooks")

    return StateConfig(
        name=name,
        type=str(raw.get("type", "agent")),
        prompt=raw.get("prompt"),
        linear_state=str(raw.get("linear_state", "active")),
        runner=str(raw.get("runner", "claude")),
        model=raw.get("model"),
        max_turns=raw.get("max_turns"),
        turn_timeout_ms=raw.get("turn_timeout_ms"),
        stall_timeout_ms=raw.get("stall_timeout_ms"),
        session=str(raw.get("session", "inherit")),
        permission_mode=raw.get("permission_mode"),
        allowed_tools=_coerce_list(allowed) if allowed is not None else None,
        rework_to=raw.get("rework_to"),
        max_rework=raw.get("max_rework"),
        transitions=raw.get("transitions") or {},
        hooks=_parse_hooks(hooks_raw) if hooks_raw else None,
    )


def merge_state_config(
    state: StateConfig, root_claude: ClaudeConfig, root_hooks: HooksConfig
) -> tuple[ClaudeConfig, HooksConfig]:
    """Merge state overrides with root defaults. Returns (claude_cfg, hooks_cfg)."""
    claude = ClaudeConfig(
        command=root_claude.command,
        permission_mode=state.permission_mode or root_claude.permission_mode,
        allowed_tools=state.allowed_tools if state.allowed_tools is not None else root_claude.allowed_tools,
        model=state.model or root_claude.model,
        max_turns=state.max_turns if state.max_turns is not None else root_claude.max_turns,
        turn_timeout_ms=state.turn_timeout_ms if state.turn_timeout_ms is not None else root_claude.turn_timeout_ms,
        stall_timeout_ms=state.stall_timeout_ms if state.stall_timeout_ms is not None else root_claude.stall_timeout_ms,
        append_system_prompt=root_claude.append_system_prompt,
    )
    hooks = state.hooks if state.hooks is not None else root_hooks
    return claude, hooks


# ── Helpers for parsing the per-project block ───────────────────────────────

def _parse_tracker(raw: dict[str, Any]) -> TrackerConfig:
    return TrackerConfig(
        kind=str(raw.get("kind", "linear")),
        endpoint=str(raw.get("endpoint", "https://api.linear.app/graphql")),
        api_key=str(raw.get("api_key", "")),
        project_slug=str(raw.get("project_slug", "")),
    )


def _parse_workspace(raw: dict[str, Any]) -> WorkspaceConfig:
    return WorkspaceConfig(root=str(raw.get("root", "")))


def _parse_full_hooks(raw: dict[str, Any]) -> HooksConfig:
    return HooksConfig(
        after_create=raw.get("after_create"),
        before_run=raw.get("before_run"),
        after_run=raw.get("after_run"),
        before_remove=raw.get("before_remove"),
        on_stage_enter=raw.get("on_stage_enter"),
        timeout_ms=_coerce_int(raw.get("timeout_ms"), 60_000),
    )


def _parse_claude(raw: dict[str, Any]) -> ClaudeConfig:
    return ClaudeConfig(
        command=str(raw.get("command", "claude")),
        permission_mode=str(raw.get("permission_mode", "auto")),
        allowed_tools=_coerce_list(raw.get("allowed_tools"))
        or ["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
        model=raw.get("model"),
        max_turns=_coerce_int(raw.get("max_turns"), 20),
        turn_timeout_ms=_coerce_int(raw.get("turn_timeout_ms"), 3_600_000),
        stall_timeout_ms=_coerce_int(raw.get("stall_timeout_ms"), 300_000),
        append_system_prompt=raw.get("append_system_prompt"),
    )


def _parse_linear_states(raw: dict[str, Any]) -> LinearStatesConfig:
    return LinearStatesConfig(
        todo=str(raw.get("todo", "Todo")),
        active=str(raw.get("active", "In Progress")),
        awaiting_ci=str(raw.get("awaiting_ci", "Awaiting CI")),
        review=str(raw.get("review", "Human Review")),
        gate_approved=str(raw.get("gate_approved", "Gate Approved")),
        rework=str(raw.get("rework", "Rework")),
        terminal=_coerce_list(raw.get("terminal")) or ["Done", "Closed", "Cancelled"],
    )


def _parse_prompts(raw: dict[str, Any]) -> PromptsConfig:
    return PromptsConfig(global_prompt=raw.get("global_prompt"))


def _parse_states(raw: dict[str, Any]) -> dict[str, StateConfig]:
    out: dict[str, StateConfig] = {}
    for state_name, state_data in raw.items():
        sd = state_data or {}
        out[state_name] = _parse_state_config(state_name, sd)
    return out


def _merge_dict(default: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow merge two YAML dicts; override wins on conflict."""
    out: dict[str, Any] = dict(default or {})
    out.update(override or {})
    return out


def _build_project(
    name: str,
    raw: dict[str, Any],
    defaults: dict[str, Any],
    workflow_dir: Path,
) -> ProjectConfig:
    """Build a ProjectConfig by merging top-level defaults with per-project overrides."""
    # tracker / workspace / hooks / prompts / states are project-scoped;
    # they may inherit nothing from top-level when `projects:` is used,
    # so use the project block directly. linear_states / claude inherit
    # from top-level defaults and are overlaid with per-project values.
    tracker_raw = raw.get("tracker", {}) or defaults.get("tracker", {}) or {}
    workspace_raw = raw.get("workspace", {}) or defaults.get("workspace", {}) or {}
    hooks_raw = raw.get("hooks", {}) or defaults.get("hooks", {}) or {}
    prompts_raw = raw.get("prompts", {}) or defaults.get("prompts", {}) or {}
    states_raw = raw.get("states") or defaults.get("states") or {}

    linear_states_raw = _merge_dict(defaults.get("linear_states"), raw.get("linear_states"))
    claude_raw = _merge_dict(defaults.get("claude"), raw.get("claude"))

    return ProjectConfig(
        name=name,
        paused=bool(raw.get("paused", False)),
        tracker=_parse_tracker(tracker_raw),
        workspace=_parse_workspace(workspace_raw),
        hooks=_parse_full_hooks(hooks_raw),
        prompts=_parse_prompts(prompts_raw),
        states=_parse_states(states_raw),
        linear_states=_parse_linear_states(linear_states_raw),
        claude=_parse_claude(claude_raw),
        workflow_dir=workflow_dir,
        max_concurrent=raw.get("max_concurrent"),
    )


def _legacy_project_name(tracker_raw: dict[str, Any], workflow_path: Path) -> str:
    """Derive a project name for a legacy single-project workflow.

    Prefer the workflow file's parent directory name (usually the repo
    name, which is what the operator thinks of the project as).
    Fall back to the project_slug prefix if there's no usable directory.
    """
    parent = workflow_path.parent.resolve().name
    if parent and parent not in (".", ""):
        return parent
    slug = str(tracker_raw.get("project_slug", "")).strip()
    if slug:
        return f"project-{slug[:8]}"
    return "default"


def parse_workflow_file(path: str | Path) -> WorkflowDefinition:
    """Parse a workflow file (.yaml/.yml or .md with front matter) into config."""
    path = Path(path)
    workflow_dir = path.parent
    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    content = path.read_text()
    config_raw: dict[str, Any] = {}
    prompt_body = ""

    # Detect format: pure YAML or markdown with front matter
    if path.suffix in (".yaml", ".yml"):
        config_raw = yaml.safe_load(content) or {}
    elif content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            config_raw = yaml.safe_load(parts[1]) or {}
            prompt_body = parts[2]
    else:
        # Try parsing as pure YAML
        config_raw = yaml.safe_load(content) or {}

    if not isinstance(config_raw, dict):
        raise ValueError("Workflow file must contain a YAML mapping")

    prompt_template = prompt_body.strip()

    # Global defaults that don't go per-project
    polling = PollingConfig(
        interval_ms=_coerce_int((config_raw.get("polling") or {}).get("interval_ms"), 30_000),
    )
    a = config_raw.get("agent", {}) or {}
    agent = AgentConfig(
        max_concurrent_agents=_coerce_int(a.get("max_concurrent_agents"), 5),
        max_retry_backoff_ms=_coerce_int(a.get("max_retry_backoff_ms"), 300_000),
        max_concurrent_agents_by_state=a.get("max_concurrent_agents_by_state") or {},
        max_concurrent_per_project=a.get("max_concurrent_per_project") or {},
    )
    s = config_raw.get("server", {}) or {}
    server = ServerConfig(port=s.get("port"))

    # Resolve projects list (multi-project) or synthesize from top-level (legacy)
    projects_raw = config_raw.get("projects")
    projects: list[ProjectConfig] = []

    if projects_raw is not None:
        # Multi-project mode. Top-level tracker/workspace/hooks/prompts/states
        # are not allowed in this mode (would be ambiguous). Top-level claude
        # and linear_states ARE allowed — they act as defaults each project
        # block can override.
        if not isinstance(projects_raw, list) or not projects_raw:
            raise ValueError("`projects:` must be a non-empty list of project blocks")
        for forbidden in ("tracker", "workspace", "hooks", "prompts", "states"):
            if forbidden in config_raw:
                raise ValueError(
                    f"Top-level `{forbidden}:` is not allowed when `projects:` is used. "
                    f"Move it under each project entry."
                )
        defaults = {
            "linear_states": config_raw.get("linear_states") or {},
            "claude": config_raw.get("claude") or {},
        }
        seen_names: set[str] = set()
        for idx, raw in enumerate(projects_raw):
            if not isinstance(raw, dict):
                raise ValueError(f"`projects[{idx}]` must be a mapping")
            name = str(raw.get("name", "")).strip()
            if not name:
                raise ValueError(f"`projects[{idx}].name` is required")
            if name in seen_names:
                raise ValueError(f"Duplicate project name: {name}")
            seen_names.add(name)
            projects.append(_build_project(name, raw, defaults, workflow_dir))
    else:
        # Legacy single-project mode. Build one ProjectConfig from top-level.
        tracker_raw = config_raw.get("tracker", {}) or {}
        synthetic_raw = {
            "tracker": tracker_raw,
            "workspace": config_raw.get("workspace") or {},
            "hooks": config_raw.get("hooks") or {},
            "prompts": config_raw.get("prompts") or {},
            "states": config_raw.get("states") or {},
            "linear_states": config_raw.get("linear_states") or {},
            "claude": config_raw.get("claude") or {},
        }
        name = _legacy_project_name(tracker_raw, path)
        projects.append(_build_project(name, synthetic_raw, {}, workflow_dir))

    # Populate top-level fields from projects[0] for backward compat.
    p0 = projects[0]
    cfg = ServiceConfig(
        tracker=p0.tracker,
        polling=polling,
        workspace=p0.workspace,
        hooks=p0.hooks,
        claude=p0.claude,
        agent=agent,
        server=server,
        linear_states=p0.linear_states,
        prompts=p0.prompts,
        states=p0.states,
        projects=projects,
        workflow_dir=workflow_dir,
    )

    return WorkflowDefinition(config=cfg, prompt_template=prompt_template)


def _validate_project(project: ProjectConfig, errors: list[str]) -> None:
    """Validate a single project's state machine and tracker."""
    prefix = f"project '{project.name}'"

    if project.tracker.kind != "linear":
        errors.append(f"{prefix}: unsupported tracker kind: {project.tracker.kind}")
    if not project.resolved_api_key():
        errors.append(f"{prefix}: missing tracker API key")
    if not project.tracker.project_slug:
        errors.append(f"{prefix}: missing tracker.project_slug")

    if not project.states:
        errors.append(f"{prefix}: no states defined")
        return

    valid_linear_keys = {"active", "awaiting_ci", "review", "gate_approved", "rework", "terminal"}
    has_agent = False
    has_terminal = False
    all_state_names = set(project.states.keys())

    for name, sc in project.states.items():
        if sc.type not in ("agent", "gate", "terminal"):
            errors.append(f"{prefix} state '{name}': invalid type: {sc.type}")
            continue

        if sc.type == "agent":
            has_agent = True
            if not sc.prompt:
                errors.append(f"{prefix} state '{name}': agent state missing 'prompt' field")

        elif sc.type == "gate":
            if not sc.rework_to:
                errors.append(f"{prefix} state '{name}': gate missing 'rework_to' field")
            elif sc.rework_to not in all_state_names:
                errors.append(
                    f"{prefix} state '{name}': rework_to target '{sc.rework_to}' "
                    f"is not a defined state"
                )
            if "approve" not in sc.transitions:
                errors.append(f"{prefix} state '{name}': gate missing 'approve' transition")

        elif sc.type == "terminal":
            has_terminal = True

        if sc.linear_state not in valid_linear_keys:
            errors.append(
                f"{prefix} state '{name}': invalid linear_state '{sc.linear_state}' "
                f"(valid: {', '.join(sorted(valid_linear_keys))})"
            )

        for trigger, target in sc.transitions.items():
            if target not in all_state_names:
                errors.append(
                    f"{prefix} state '{name}': transition '{trigger}' points to "
                    f"unknown state '{target}'"
                )

    if not has_agent:
        errors.append(f"{prefix}: no agent states defined")
    if not has_terminal:
        errors.append(f"{prefix}: no terminal states defined")

    # Warn about unreachable states
    entry = project.entry_state
    reachable: set[str] = set()
    if entry:
        reachable.add(entry)
    for sc in project.states.values():
        for target in sc.transitions.values():
            reachable.add(target)
        if sc.rework_to:
            reachable.add(sc.rework_to)
    for name in all_state_names - reachable:
        log.warning("project '%s' state '%s' is unreachable", project.name, name)


def validate_config(cfg: ServiceConfig) -> list[str]:
    """Validate state machine config for dispatch readiness. Returns list of errors."""
    errors: list[str] = []
    if not cfg.projects:
        errors.append("No projects defined")
        return errors
    for project in cfg.projects:
        _validate_project(project, errors)
    return errors
