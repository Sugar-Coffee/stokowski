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
    mode: str = "clone"  # "clone" or "worktree"
    repo_path: str = ""  # required for worktree mode — path to the git repo

    def resolved_root(self) -> Path:
        if self.root:
            return Path(os.path.expandvars(os.path.expanduser(self.root)))
        return Path(tempfile.gettempdir()) / "stokowski_workspaces"

    def resolved_repo_path(self) -> Path | None:
        if self.repo_path:
            return Path(os.path.expandvars(os.path.expanduser(self.repo_path)))
        return None


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
    headless_prompt: str | None = None  # None = disabled, set to override default headless context


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 5
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)


@dataclass
class ServerConfig:
    port: int | None = None


@dataclass
class LinearStatesConfig:
    """Maps logical state names to actual Linear state names."""
    todo: str = "Todo"
    active: str = "In Progress"
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
class WorkflowDefinition:
    config: ServiceConfig
    prompt_template: str


@dataclass
class ServiceConfig:
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

    def resolved_api_key(self) -> str:
        key = self.tracker.api_key
        if not key:
            return os.environ.get("LINEAR_API_KEY", "")
        if key.startswith("$"):
            return os.environ.get(key[1:], "")
        return key

    def agent_env(self) -> dict[str, str]:
        """Build env vars to pass to agent subprocesses.

        Includes the parent process env plus Linear config from workflow.yaml,
        so agents can connect to Linear using the same credentials as Stokowski.
        """
        env = dict(os.environ)
        api_key = self.resolved_api_key()
        if api_key:
            env["LINEAR_API_KEY"] = api_key
        if self.tracker.project_slug:
            env["LINEAR_PROJECT_SLUG"] = self.tracker.project_slug
        if self.tracker.endpoint:
            env["LINEAR_ENDPOINT"] = self.tracker.endpoint
        return env

    @property
    def entry_state(self) -> str | None:
        """Return the first agent state (first key in states dict)."""
        for name, sc in self.states.items():
            if sc.type == "agent":
                return name
        return None

    def active_linear_states(self) -> list[str]:
        """Return Linear state names that should be polled for candidates.

        Includes the todo state (pickup) and all agent state mappings.
        """
        ls = self.linear_states
        seen: list[str] = []
        # Always include the todo state so new issues get picked up
        if ls.todo and ls.todo not in seen:
            seen.append(ls.todo)
        for sc in self.states.values():
            if sc.type == "agent":
                linear_name = _resolve_linear_state_name(sc.linear_state, ls)
                if linear_name and linear_name not in seen:
                    seen.append(linear_name)
        return seen

    def gate_linear_states(self) -> list[str]:
        """Return Linear state names for all gate states."""
        ls = self.linear_states
        seen: list[str] = []
        for sc in self.states.values():
            if sc.type == "gate":
                linear_name = _resolve_linear_state_name(sc.linear_state, ls)
                if linear_name and linear_name not in seen:
                    seen.append(linear_name)
        return seen

    def terminal_linear_states(self) -> list[str]:
        """Return the terminal Linear state names."""
        return list(self.linear_states.terminal)


def _resolve_linear_state_name(key: str, ls: LinearStatesConfig) -> str:
    """Resolve a logical state key to the actual Linear state name."""
    mapping: dict[str, str] = {
        "active": ls.active,
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


def parse_workflow_file(path: str | Path) -> WorkflowDefinition:
    """Parse a workflow file (.yaml/.yml or .md with front matter) into config."""
    path = Path(path)
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

    # Parse tracker
    t = config_raw.get("tracker", {}) or {}
    tracker = TrackerConfig(
        kind=str(t.get("kind", "linear")),
        endpoint=str(t.get("endpoint", "https://api.linear.app/graphql")),
        api_key=str(t.get("api_key", "")),
        project_slug=str(t.get("project_slug", "")),
    )

    # Parse polling
    p = config_raw.get("polling", {}) or {}
    polling = PollingConfig(interval_ms=_coerce_int(p.get("interval_ms"), 30_000))

    # Parse workspace
    w = config_raw.get("workspace", {}) or {}
    workspace = WorkspaceConfig(
        root=str(w.get("root", "")),
        mode=str(w.get("mode", "clone")),
        repo_path=str(w.get("repo_path", "")),
    )

    # Parse hooks
    h = config_raw.get("hooks", {}) or {}
    hooks = HooksConfig(
        after_create=h.get("after_create"),
        before_run=h.get("before_run"),
        after_run=h.get("after_run"),
        before_remove=h.get("before_remove"),
        on_stage_enter=h.get("on_stage_enter"),
        timeout_ms=_coerce_int(h.get("timeout_ms"), 60_000),
    )

    # Parse claude
    c = config_raw.get("claude", {}) or {}
    claude = ClaudeConfig(
        command=str(c.get("command", "claude")),
        permission_mode=str(c.get("permission_mode", "auto")),
        allowed_tools=_coerce_list(c.get("allowed_tools"))
        or ["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
        model=c.get("model"),
        max_turns=_coerce_int(c.get("max_turns"), 20),
        turn_timeout_ms=_coerce_int(c.get("turn_timeout_ms"), 3_600_000),
        stall_timeout_ms=_coerce_int(c.get("stall_timeout_ms"), 300_000),
        append_system_prompt=c.get("append_system_prompt"),
        headless_prompt=c.get("headless_prompt"),
    )

    # Parse agent
    a = config_raw.get("agent", {}) or {}
    agent = AgentConfig(
        max_concurrent_agents=_coerce_int(a.get("max_concurrent_agents"), 5),
        max_retry_backoff_ms=_coerce_int(a.get("max_retry_backoff_ms"), 300_000),
        max_concurrent_agents_by_state=a.get("max_concurrent_agents_by_state") or {},
    )

    # Parse server
    s = config_raw.get("server", {}) or {}
    server = ServerConfig(port=s.get("port"))

    # Parse linear_states
    ls_raw = config_raw.get("linear_states", {}) or {}
    linear_states = LinearStatesConfig(
        todo=str(ls_raw.get("todo", "Todo")),
        active=str(ls_raw.get("active", "In Progress")),
        review=str(ls_raw.get("review", "Human Review")),
        gate_approved=str(ls_raw.get("gate_approved", "Gate Approved")),
        rework=str(ls_raw.get("rework", "Rework")),
        terminal=_coerce_list(ls_raw.get("terminal")) or ["Done", "Closed", "Cancelled"],
    )

    # Parse prompts
    pr_raw = config_raw.get("prompts", {}) or {}
    prompts = PromptsConfig(
        global_prompt=pr_raw.get("global_prompt"),
    )

    # Parse states
    states_raw = config_raw.get("states", {}) or {}
    states: dict[str, StateConfig] = {}
    for state_name, state_data in states_raw.items():
        sd = state_data or {}
        states[state_name] = _parse_state_config(state_name, sd)

    cfg = ServiceConfig(
        tracker=tracker,
        polling=polling,
        workspace=workspace,
        hooks=hooks,
        claude=claude,
        agent=agent,
        server=server,
        linear_states=linear_states,
        prompts=prompts,
        states=states,
    )

    return WorkflowDefinition(config=cfg, prompt_template=prompt_template)


def validate_config(cfg: ServiceConfig) -> list[str]:
    """Validate state machine config for dispatch readiness. Returns list of errors."""
    errors: list[str] = []

    # Basic tracker checks
    if cfg.tracker.kind != "linear":
        errors.append(f"Unsupported tracker kind: {cfg.tracker.kind}")
    if not cfg.resolved_api_key():
        errors.append("Missing tracker API key (set LINEAR_API_KEY or tracker.api_key)")
    if not cfg.tracker.project_slug:
        errors.append("Missing tracker.project_slug")

    if not cfg.states:
        errors.append("No states defined")
        return errors

    # Valid linear_state keys
    valid_linear_keys = {"active", "review", "gate_approved", "rework", "terminal"}

    has_agent = False
    has_terminal = False
    all_state_names = set(cfg.states.keys())

    for name, sc in cfg.states.items():
        # Check type
        if sc.type not in ("agent", "gate", "terminal"):
            errors.append(f"State '{name}' has invalid type: {sc.type}")
            continue

        if sc.type == "agent":
            has_agent = True
            # Agent states should have a prompt
            if not sc.prompt:
                errors.append(f"Agent state '{name}' is missing 'prompt' field")

        elif sc.type == "gate":
            # Gates must have rework_to
            if not sc.rework_to:
                errors.append(f"Gate state '{name}' is missing 'rework_to' field")
            elif sc.rework_to not in all_state_names:
                errors.append(
                    f"Gate state '{name}' rework_to target '{sc.rework_to}' "
                    f"is not a defined state"
                )
            # Gates must have approve transition
            if "approve" not in sc.transitions:
                errors.append(f"Gate state '{name}' is missing 'approve' transition")

        elif sc.type == "terminal":
            has_terminal = True

        # Validate linear_state key
        if sc.linear_state not in valid_linear_keys:
            errors.append(
                f"State '{name}' has invalid linear_state: '{sc.linear_state}' "
                f"(valid: {', '.join(sorted(valid_linear_keys))})"
            )

        # Validate all transitions point to existing states
        for trigger, target in sc.transitions.items():
            if target not in all_state_names:
                errors.append(
                    f"State '{name}' transition '{trigger}' points to "
                    f"unknown state '{target}'"
                )

    if not has_agent:
        errors.append("No agent states defined (need at least one state with type 'agent')")
    if not has_terminal:
        errors.append("No terminal states defined (need at least one state with type 'terminal')")

    # Warn about unreachable states (non-entry states that no transition points to)
    entry = cfg.entry_state
    reachable: set[str] = set()
    if entry:
        reachable.add(entry)
    for sc in cfg.states.values():
        for target in sc.transitions.values():
            reachable.add(target)
        if sc.rework_to:
            reachable.add(sc.rework_to)

    unreachable = all_state_names - reachable
    for name in unreachable:
        log.warning("State '%s' is unreachable (no transitions lead to it)", name)

    return errors
