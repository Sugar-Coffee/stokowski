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
    team_key: str = ""  # e.g. "DEV" — filter by team instead of/alongside project
    # GitHub-specific fields
    github_owner: str = ""        # repo owner, e.g. "my-org"
    github_repo: str = ""         # repo name, e.g. "my-project"
    github_token: str = ""        # PAT or GitHub App token


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


@dataclass
class AgentConfig:
    max_concurrent_agents: int = 5
    max_retry_backoff_ms: int = 300_000
    max_concurrent_agents_by_state: dict[str, int] = field(default_factory=dict)
    max_concurrent_by_project: dict[str, int] = field(default_factory=dict)  # per-project per-state limits


@dataclass
class ServerConfig:
    port: int | None = None


@dataclass
class WebhookConfig:
    """Webhook listener for instant tracker event reactions."""
    secret: str = ""  # HMAC-SHA256 signing secret for verification
    # GitHub repo for PR event integration (if tracker is not github)
    github_owner: str = ""
    github_repo: str = ""


@dataclass
class ScheduleConfig:
    """Auto-create tracker issues on a cron schedule via external command."""
    cron: str = ""                     # cron expression, e.g. "0 9 * * *"
    create_command: str = ""           # shell command to create the issue
    # {date} and {datetime} placeholders are replaced in create_command


@dataclass
class LinearStatesConfig:
    """Maps logical state names to actual Linear state names."""
    todo: str = "Todo"
    active: str = "In Progress"
    review: str = "Human Review"
    gate_approved: str = "Gate Approved"
    rework: str = "Rework"
    blocked: str = "Blocked"  # issues agents can't handle
    terminal: list[str] = field(default_factory=lambda: ["Done", "Closed", "Cancelled"])


@dataclass
class GitHubStatesConfig:
    """Maps logical state names to GitHub labels used as state markers."""
    todo: str = "Todo"
    active: str = "In Progress"
    review: str = "Human Review"
    gate_approved: str = "Gate Approved"
    rework: str = "Rework"
    blocked: str = "Blocked"
    terminal: list[str] = field(default_factory=lambda: ["Done"])
    close_on_terminal: bool = True  # close the issue when it reaches a terminal state


@dataclass
class PromptsConfig:
    """Prompt file references."""
    global_prompt: str | None = None


@dataclass
class RoutingRule:
    """Route issues to different entry states based on labels."""
    labels: list[str] = field(default_factory=list)  # match any of these labels
    entry_state: str = ""  # override entry state for matching issues


@dataclass
class StateConfig:
    """A single state in the state machine."""
    name: str = ""
    type: str = "agent"              # "agent", "gate", "terminal"
    prompt: str | None = None        # path to prompt .md file
    linear_state: str = "active"     # key into LinearStatesConfig
    runner: str = "claude"
    fallback_runners: list[str] = field(default_factory=list)  # e.g. ["gemini", "codex"] — tried in order on rate limit errors
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
    # PR-driven transitions for gates — maps GitHub PR events to gate actions
    # e.g. pr_triggers: {approved: approve, changes_requested: rework, merged: complete}
    pr_triggers: dict[str, str] = field(default_factory=dict)


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
    routing: list[RoutingRule] = field(default_factory=list)
    schedule: ScheduleConfig | None = None
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    github_states: GitHubStatesConfig = field(default_factory=GitHubStatesConfig)

    def resolved_api_key(self) -> str:
        """Resolve tracker API key/token from config or environment."""
        if self.tracker.kind == "github":
            key = self.tracker.github_token
            if not key:
                return os.environ.get("GITHUB_TOKEN", "")
            if key.startswith("$"):
                return os.environ.get(key[1:], "")
            return key
        # Linear
        key = self.tracker.api_key
        if not key:
            return os.environ.get("LINEAR_API_KEY", "")
        if key.startswith("$"):
            return os.environ.get(key[1:], "")
        return key

    def agent_env(self) -> dict[str, str]:
        """Build env vars to pass to agent subprocesses.

        Includes the parent process env plus tracker config from workflow.yaml,
        so agents can connect to the tracker using the same credentials as Stokowski.
        """
        env = dict(os.environ)
        if self.tracker.kind == "github":
            token = self.resolved_api_key()
            if token:
                env["GITHUB_TOKEN"] = token
            if self.tracker.github_owner:
                env["GITHUB_OWNER"] = self.tracker.github_owner
            if self.tracker.github_repo:
                env["GITHUB_REPO"] = self.tracker.github_repo
        else:
            api_key = self.resolved_api_key()
            if api_key:
                env["LINEAR_API_KEY"] = api_key
            if self.tracker.project_slug:
                env["LINEAR_PROJECT_SLUG"] = self.tracker.project_slug
            if self.tracker.team_key:
                env["LINEAR_TEAM_KEY"] = self.tracker.team_key
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

    def entry_state_for_issue(self, labels: list[str]) -> str | None:
        """Resolve entry state based on issue labels and routing rules.

        Returns the entry_state from the first matching routing rule,
        or the default entry_state if no rules match.
        """
        if self.routing:
            issue_labels = {l.lower() for l in labels}
            for rule in self.routing:
                rule_labels = {l.lower() for l in rule.labels}
                if issue_labels & rule_labels:  # any overlap
                    if rule.entry_state in self.states:
                        return rule.entry_state
        return self.entry_state

    @property
    def _states_cfg(self) -> LinearStatesConfig | GitHubStatesConfig:
        """Return the active states config based on tracker kind."""
        if self.tracker.kind == "github":
            return self.github_states
        return self.linear_states

    def active_linear_states(self) -> list[str]:
        """Return tracker state names that should be polled for candidates.

        Includes the todo state (pickup) and all agent state mappings.
        """
        sc_cfg = self._states_cfg
        seen: list[str] = []
        if sc_cfg.todo and sc_cfg.todo not in seen:
            seen.append(sc_cfg.todo)
        for sc in self.states.values():
            if sc.type == "agent":
                state_name = _resolve_state_name(sc.linear_state, sc_cfg)
                if state_name and state_name not in seen:
                    seen.append(state_name)
        return seen

    def gate_linear_states(self) -> list[str]:
        """Return tracker state names for all gate states."""
        sc_cfg = self._states_cfg
        seen: list[str] = []
        for sc in self.states.values():
            if sc.type == "gate":
                state_name = _resolve_state_name(sc.linear_state, sc_cfg)
                if state_name and state_name not in seen:
                    seen.append(state_name)
        return seen

    def terminal_linear_states(self) -> list[str]:
        """Return the terminal tracker state names."""
        return list(self._states_cfg.terminal)


def _resolve_state_name(key: str, sc: LinearStatesConfig | GitHubStatesConfig) -> str:
    """Resolve a logical state key to the actual tracker state name."""
    mapping: dict[str, str] = {
        "active": sc.active,
        "review": sc.review,
        "gate_approved": sc.gate_approved,
        "rework": sc.rework,
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
        fallback_runners=_coerce_list(raw.get("fallback_runners")),
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
        pr_triggers=raw.get("pr_triggers") or {},
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


def is_root_config(path: str | Path) -> bool:
    """Check if a YAML file is a root config (has 'workflows:' key)."""
    path = Path(path)
    if not path.exists():
        return False
    try:
        raw = yaml.safe_load(path.read_text())
        return isinstance(raw, dict) and "workflows" in raw
    except Exception:
        return False


@dataclass
class RootConfig:
    """Parsed root config with shared settings and workflow paths."""
    workflow_paths: dict[str, Path]
    shared_raw: dict[str, Any]  # shared config sections to merge into workflows


# Sections that live in the root config (shared across workflows)
_SHARED_SECTIONS = {
    "tracker", "linear_states", "github_states", "workspace", "hooks",
    "claude", "agent", "server", "webhook",
}


def parse_root_config(path: str | Path) -> RootConfig:
    """Parse a root config with a 'workflows:' key.

    Returns RootConfig with workflow paths and shared config sections.
    Shared sections (tracker, workspace, hooks, etc.) are extracted from
    the root YAML and merged into each workflow when parsed.
    """
    path = Path(path)
    content = path.read_text()
    raw = yaml.safe_load(content)
    if not isinstance(raw, dict) or "workflows" not in raw:
        raise ValueError("Not a root config (missing 'workflows:' key)")

    base_dir = path.parent
    workflow_paths: dict[str, Path] = {}
    for name, entry in raw["workflows"].items():
        if isinstance(entry, str):
            wf_path = entry
        elif isinstance(entry, dict):
            wf_path = str(entry.get("path", ""))
        else:
            raise ValueError(f"Invalid workflow entry '{name}'")
        resolved = (base_dir / wf_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Workflow '{name}' not found: {resolved}")
        workflow_paths[name] = resolved

    # Extract shared sections
    shared_raw = {k: v for k, v in raw.items() if k in _SHARED_SECTIONS and v}

    return RootConfig(workflow_paths=workflow_paths, shared_raw=shared_raw)


def parse_workflow_file(
    path: str | Path,
    shared_raw: dict[str, Any] | None = None,
) -> WorkflowDefinition:
    """Parse a workflow file (.yaml/.yml or .md with front matter) into config.

    If shared_raw is provided (from a root config), shared sections are used
    as defaults — the workflow file can override any shared section.
    """
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

    # Merge shared config as defaults (workflow overrides shared)
    if shared_raw:
        merged = dict(shared_raw)
        for k, v in config_raw.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                # Deep merge one level: workflow keys override shared keys
                merged_section = dict(merged[k])
                merged_section.update(v)
                merged[k] = merged_section
            else:
                merged[k] = v
        config_raw = merged

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
        team_key=str(t.get("team_key", "")),
        github_owner=str(t.get("github_owner", "")),
        github_repo=str(t.get("github_repo", "")),
        github_token=str(t.get("github_token", "")),
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
    )

    # Parse agent
    a = config_raw.get("agent", {}) or {}
    agent = AgentConfig(
        max_concurrent_agents=_coerce_int(a.get("max_concurrent_agents"), 5),
        max_retry_backoff_ms=_coerce_int(a.get("max_retry_backoff_ms"), 300_000),
        max_concurrent_agents_by_state=a.get("max_concurrent_agents_by_state") or {},
        max_concurrent_by_project=a.get("max_concurrent_by_project") or {},
    )

    # Parse server
    s = config_raw.get("server", {}) or {}
    server = ServerConfig(port=s.get("port"))

    # Parse webhook
    wh = config_raw.get("webhook", {}) or {}
    webhook = WebhookConfig(
        secret=_resolve_env(str(wh.get("secret", ""))),
        github_owner=str(wh.get("github_owner", "")),
        github_repo=str(wh.get("github_repo", "")),
    )

    # Parse linear_states
    ls_raw = config_raw.get("linear_states", {}) or {}
    linear_states = LinearStatesConfig(
        todo=str(ls_raw.get("todo", "Todo")),
        active=str(ls_raw.get("active", "In Progress")),
        review=str(ls_raw.get("review", "Human Review")),
        gate_approved=str(ls_raw.get("gate_approved", "Gate Approved")),
        rework=str(ls_raw.get("rework", "Rework")),
        blocked=str(ls_raw.get("blocked", "Blocked")),
        terminal=_coerce_list(ls_raw.get("terminal")) or ["Done", "Closed", "Cancelled"],
    )

    # Parse github_states
    gs_raw = config_raw.get("github_states", {}) or {}
    github_states = GitHubStatesConfig(
        todo=str(gs_raw.get("todo", "Todo")),
        active=str(gs_raw.get("active", "In Progress")),
        review=str(gs_raw.get("review", "Human Review")),
        gate_approved=str(gs_raw.get("gate_approved", "Gate Approved")),
        rework=str(gs_raw.get("rework", "Rework")),
        blocked=str(gs_raw.get("blocked", "Blocked")),
        terminal=_coerce_list(gs_raw.get("terminal")) or ["Done"],
        close_on_terminal=gs_raw.get("close_on_terminal", True),
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

    # Parse routing rules
    routing_raw = config_raw.get("routing", []) or []
    routing: list[RoutingRule] = []
    for rule_data in routing_raw:
        if isinstance(rule_data, dict):
            routing.append(RoutingRule(
                labels=_coerce_list(rule_data.get("labels")),
                entry_state=str(rule_data.get("entry_state", "")),
            ))

    # Parse schedule
    sched_raw = config_raw.get("schedule")
    schedule: ScheduleConfig | None = None
    if sched_raw and isinstance(sched_raw, dict):
        schedule = ScheduleConfig(
            cron=str(sched_raw.get("cron", "")),
            create_command=str(sched_raw.get("create_command", "")),
        )

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
        routing=routing,
        schedule=schedule,
        webhook=webhook,
        github_states=github_states,
    )

    return WorkflowDefinition(config=cfg, prompt_template=prompt_template)


def validate_config(cfg: ServiceConfig) -> list[str]:
    """Validate state machine config for dispatch readiness. Returns list of errors."""
    errors: list[str] = []

    # Basic tracker checks
    if cfg.tracker.kind not in ("linear", "github"):
        errors.append(f"Unsupported tracker kind: {cfg.tracker.kind}")
    if not cfg.resolved_api_key():
        if cfg.tracker.kind == "github":
            errors.append("Missing tracker token (set GITHUB_TOKEN or tracker.github_token)")
        else:
            errors.append("Missing tracker API key (set LINEAR_API_KEY or tracker.api_key)")
    if cfg.tracker.kind == "github":
        if not cfg.tracker.github_owner or not cfg.tracker.github_repo:
            errors.append("GitHub tracker requires tracker.github_owner and tracker.github_repo")
    elif not cfg.tracker.project_slug and not cfg.tracker.team_key:
        errors.append("Missing tracker.project_slug or tracker.team_key (at least one required)")

    if not cfg.states:
        errors.append("No states defined")
        return errors

    # Valid linear_state keys
    valid_linear_keys = {"todo", "active", "review", "gate_approved", "rework", "blocked", "terminal"}

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

    # Validate routing rules
    for i, rule in enumerate(cfg.routing):
        if not rule.labels:
            errors.append(f"Routing rule {i} has no labels")
        if rule.entry_state and rule.entry_state not in all_state_names:
            errors.append(f"Routing rule {i} entry_state '{rule.entry_state}' is not a defined state")

    # Validate schedule config
    if cfg.schedule:
        if not cfg.schedule.cron:
            errors.append("Schedule defined but missing 'cron' field")
        else:
            try:
                from croniter import croniter
                croniter(cfg.schedule.cron)
            except ImportError:
                errors.append(
                    "Schedule requires 'croniter' package: pip install stokowski[schedule]"
                )
            except (ValueError, KeyError) as e:
                errors.append(f"Invalid cron expression '{cfg.schedule.cron}': {e}")
        if not cfg.schedule.create_command:
            errors.append("Schedule defined but missing 'create_command' field")

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
