"""Config editing behind the dashboard.

A workflow is a state machine spread across a YAML file and a handful of
markdown prompts. Reading it end to end tells you what it does; glancing at it
does not. This module backs a UI that shows the pipeline at a glance and lets
the obvious knobs — model, turn limits, concurrency — be changed without
opening an editor.

Two properties matter more than the feature itself:

**Comments survive.** The comments in a workflow file are its documentation.
A naive load-and-dump destroys them — measured on the shipped example, PyYAML
keeps 7 of 193 comments and collapses 310 lines to 131. So round-tripping goes
through ruamel with the indentation configured to match the hand-written
style, which reproduces the file byte for byte.

**An invalid config is never written.** Every edit is rendered, parsed and
validated in a temp file first; only a config that Stokowski could actually run
is committed, and the commit itself is atomic. A dashboard that can leave the
orchestrator unable to start is worse than no dashboard.

Edits are confined to a whitelist of scalar fields. Structural changes — adding
states, rewiring transitions — stay in the file, where a diff shows what
happened.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import parse_workflow_file, validate_config
from .model_catalogue import EFFORT_LEVELS, catalogue

logger = logging.getLogger("stokowski.studio")


def _yaml() -> YAML:
    """A round-trip loader that reproduces hand-written formatting."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # never re-wrap a long line
    y.indent(mapping=2, sequence=4, offset=2)
    return y


# ── What may be edited ───────────────────────────────────────────────────────
#
# (type, choices). `choices` of None means free entry. Anything not listed here
# is read-only through the UI — structural edits belong in the file, where a
# diff makes them reviewable.

_INT = ("int", None)
_STR = ("str", None)
_FLOAT = ("float", None)
_MODEL = ("model", None)      # rendered as a grouped dropdown, free text allowed
_EFFORT = ("str", EFFORT_LEVELS)

ROOT_FIELDS: dict[str, tuple[str, list[str] | None]] = {
    "polling.interval_ms": _INT,
    "agent.max_concurrent_agents": _INT,
    "agent.max_retry_backoff_ms": _INT,
    "claude.model": _MODEL,
    "claude.effort": _EFFORT,
    "claude.max_budget_usd": _FLOAT,
    "claude.fallback_model": _MODEL,
    "claude.turn_timeout_ms": _INT,
    "claude.stall_timeout_ms": _INT,
    "claude.permission_mode": ("str", ["auto", "allowedTools", "default"]),
    "hooks.timeout_ms": _INT,
    "server.port": _INT,
}

STATE_FIELDS: dict[str, tuple[str, list[str] | None]] = {
    "model": _MODEL,
    "effort": _EFFORT,
    # The real runaway guard. `max_turns` is NOT one — the CLI has no
    # --max-turns flag, and in state machine mode each dispatch is a single
    # invocation, so the value is inert. It stays editable for legacy
    # multi-turn workflows only.
    "max_budget_usd": _FLOAT,
    "session": ("str", ["inherit", "fresh"]),
    "runner": ("str", ["claude", "codex"]),
    "prompt": _STR,
    "max_rework": _INT,
    "rework_to": _STR,
}


class StudioError(Exception):
    """A rejected edit. The message is shown to the user."""


class Studio:
    def __init__(self, workflow_path: Path):
        self.workflow_path = Path(workflow_path).resolve()
        self.root = self.workflow_path.parent

    # ── Reading ──────────────────────────────────────────────────────────

    def _load(self) -> Any:
        y = _yaml()
        return y.load(self.workflow_path.read_text())

    def describe(self, workflow: str | None = None) -> dict[str, Any]:
        """The pipeline as the UI needs it: stages, wiring, editable fields.

        With `workflows/` files present the studio shows one at a time; the
        inline `states:` block is shown when there are none, so a
        single-pipeline config looks exactly as it did before.
        """
        data = self._load()
        available = self._list_workflows()
        selected = workflow if workflow in available else (
            self._default_workflow(data) or (available[0] if available else None)
        )

        if selected:
            states_raw = self._workflow_doc(selected).get("states") or {}
        else:
            states_raw = data.get("states") or {}

        states = []
        for name, cfg in states_raw.items():
            cfg = cfg or {}
            states.append({
                "name": name,
                "type": cfg.get("type", "agent"),
                "linear_state": cfg.get("linear_state"),
                "prompt": cfg.get("prompt"),
                "model": cfg.get("model"),
                "effort": cfg.get("effort"),
                "max_budget_usd": cfg.get("max_budget_usd"),
                "max_turns": cfg.get("max_turns"),
                "session": cfg.get("session"),
                "runner": cfg.get("runner"),
                "rework_to": cfg.get("rework_to"),
                "max_rework": cfg.get("max_rework"),
                "transitions": dict(cfg.get("transitions") or {}),
                # Per-state concurrency lives in a separate map; surface it here
                # so the UI can show it beside the state it limits.
                "concurrency": ((data.get("agent") or {}).get(
                    "max_concurrent_agents_by_state") or {}).get(name),
            })

        # Every model the workflow already names, so an operator running one
        # newer than the catalogue still sees their choice in the dropdown.
        in_use = [s.get("model") for s in states if s.get("model")]
        root_claude = data.get("claude") or {}
        in_use += [root_claude.get("model"), root_claude.get("fallback_model")]

        routing_raw = data.get("routing") or {}
        return {
            "workflow_path": str(self.workflow_path),
            "workflows": available,
            "selected_workflow": selected,
            "routing": {
                "default": routing_raw.get("default"),
                "rules": [
                    {"label": r.get("label"), "workflow": r.get("workflow")}
                    for r in (routing_raw.get("rules") or [])
                    if isinstance(r, dict)
                ],
            },
            "model_catalogue": catalogue(in_use=[m for m in in_use if m]),
            "root": {k: _dig(data, k) for k in ROOT_FIELDS},
            "root_fields": {k: {"type": t, "choices": c} for k, (t, c) in ROOT_FIELDS.items()},
            "state_fields": {k: {"type": t, "choices": c} for k, (t, c) in STATE_FIELDS.items()},
            "states": states,
            "entry_state": next((s["name"] for s in states if s["type"] == "agent"), None),
            "prompts": self.list_prompts(),
            "linear_states": dict(data.get("linear_states") or {}),
        }

    # ── Workflow files ───────────────────────────────────────────────────

    def _workflows_dir(self) -> Path:
        return self.root / "workflows"

    def _list_workflows(self) -> list[str]:
        """Names of every workflow file, real files shadowing examples."""
        directory = self._workflows_dir()
        if not directory.is_dir():
            return []
        names = set()
        for path in directory.glob("*.y*ml"):
            if path.name.startswith("."):
                continue
            stem = path.stem
            names.add(stem[: -len(".example")] if stem.endswith(".example") else stem)
        return sorted(names)

    def _workflow_path(self, name: str) -> Path:
        """Resolve a workflow name to a file, preferring a real one."""
        if name not in self._list_workflows():
            raise StudioError(f"No such workflow: {name}")
        directory = self._workflows_dir()
        for candidate in (f"{name}.yaml", f"{name}.yml",
                          f"{name}.example.yaml", f"{name}.example.yml"):
            path = directory / candidate
            if path.is_file():
                return path
        raise StudioError(f"No such workflow: {name}")

    def _workflow_doc(self, name: str) -> Any:
        return _yaml().load(self._workflow_path(name).read_text())

    def _default_workflow(self, data: Any) -> str | None:
        return (data.get("routing") or {}).get("default")

    def set_default_workflow(self, name: str) -> dict[str, Any]:
        """Point routing.default at a workflow, validating before writing."""
        if name not in self._list_workflows():
            raise StudioError(f"No such workflow: {name}")
        data = self._load()
        routing = data.get("routing")
        if routing is None:
            raise StudioError(
                "This config has no routing block — add one before setting a default"
            )
        routing["default"] = name
        rendered = _render(data)
        self._validate_or_raise(rendered)
        _atomic_write(self.workflow_path, rendered)
        logger.info(f"studio: default workflow set to {name}")
        return {"applied": ["routing.default"]}


    def list_prompts(self) -> list[dict[str, Any]]:
        """Every markdown prompt under the workflow directory."""
        found = []
        for path in sorted(self.root.rglob("*.md")):
            if any(part.startswith(".") for part in path.parts):
                continue
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                continue
            found.append({
                "path": str(rel),
                "name": path.name,
                "bytes": path.stat().st_size,
            })
        return found

    def read_prompt(self, rel_path: str) -> str:
        return self._prompt_path(rel_path).read_text()

    def raw(self) -> str:
        return self.workflow_path.read_text()

    # ── Writing ──────────────────────────────────────────────────────────

    def _prompt_path(self, rel_path: str) -> Path:
        """Resolve a prompt path, refusing anything outside the workflow dir."""
        if rel_path.endswith((".yaml", ".yml")) or not rel_path.endswith(".md"):
            raise StudioError("Only .md prompt files can be edited here")
        candidate = (self.root / rel_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise StudioError("Path escapes the workflow directory")
        if not candidate.is_file():
            raise StudioError(f"No such prompt: {rel_path}")
        return candidate

    def write_prompt(self, rel_path: str, body: str) -> None:
        path = self._prompt_path(rel_path)
        _atomic_write(path, body)
        logger.info(f"studio: wrote prompt {rel_path}")

    def apply(self, updates: list[dict[str, Any]], workflow: str | None = None) -> dict[str, Any]:
        """Apply scalar edits, then validate, then commit atomically.

        `updates` are `{"scope": "root"|"state", "state": name, "field": ..., "value": ...}`.
        """
        if not updates:
            raise StudioError("No changes supplied")

        # State edits belong to the workflow file that owns the state; root
        # edits always belong to the main config.
        target = self._workflow_path(workflow) if workflow else self.workflow_path
        data = _yaml().load(target.read_text()) if workflow else self._load()
        applied = []

        for update in updates:
            scope = update.get("scope")
            field = str(update.get("field") or "")
            value = update.get("value")

            if scope == "root":
                spec = ROOT_FIELDS.get(field)
                if not spec:
                    raise StudioError(f"Field '{field}' is not editable")
                _put(data, field, _coerce(field, value, spec))
                applied.append(field)

            elif scope == "state":
                name = str(update.get("state") or "")
                states = data.get("states") or {}
                if name not in states:
                    raise StudioError(f"No such state: {name}")
                spec = STATE_FIELDS.get(field)
                if not spec:
                    raise StudioError(f"Field '{field}' is not editable")
                coerced = _coerce(f"{name}.{field}", value, spec)
                if coerced is None:
                    states[name].pop(field, None)
                else:
                    states[name][field] = coerced
                applied.append(f"{name}.{field}")

            else:
                raise StudioError(f"Unknown scope: {scope!r}")

        rendered = _render(data)
        if workflow:
            # A workflow file is not a whole config, so validate by writing it
            # in place and re-parsing the real config around it.
            self._validate_workflow_or_raise(target, rendered)
        else:
            self._validate_or_raise(rendered)
        _atomic_write(target, rendered)
        logger.info(f"studio: updated {', '.join(applied)}")
        return {"applied": applied}

    def _validate_workflow_or_raise(self, target: Path, rendered: str) -> None:
        """Validate a candidate workflow file against the surrounding config.

        Written in place behind a backup rather than to a temp name, because a
        workflow's identity is its filename — validating a copy under a
        different name would validate a different workflow.
        """
        original = target.read_text()
        try:
            _atomic_write(target, rendered)
            self._validate_or_raise(self.workflow_path.read_text())
        finally:
            _atomic_write(target, original)

    def write_raw(self, text: str) -> dict[str, Any]:
        """Replace the whole file, for when the UI is not enough.

        Still validated first — the escape hatch does not get to skip the one
        guarantee that matters.
        """
        self._validate_or_raise(text)
        _atomic_write(self.workflow_path, text)
        logger.info("studio: wrote workflow.yaml wholesale")
        return {"applied": ["workflow.yaml"]}

    def _validate_or_raise(self, rendered: str) -> None:
        """Parse and validate a candidate config before it touches disk.

        Written into the real workflow directory (under a temp name) so that
        relative prompt paths resolve exactly as they will at runtime.
        """
        tmp = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=self.root, prefix=".stokowski-validate-", suffix=".yaml"
            )
            os.close(fd)
            tmp = Path(tmp_name)
            tmp.write_text(rendered)

            try:
                cfg = parse_workflow_file(str(tmp)).config
            except Exception as e:
                raise StudioError(f"Config would not parse: {e}") from e

            errors = validate_config(cfg)
            if errors:
                raise StudioError("Config would be invalid: " + "; ".join(map(str, errors)))
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


# ── helpers ──────────────────────────────────────────────────────────────────


def _render(data: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def _dig(data: Any, dotted: str) -> Any:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _put(data: Any, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            raise StudioError(f"Cannot set '{dotted}' — '{part}' is missing")
        node = node[part]
    if value is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value


def _coerce(label: str, value: Any, spec: tuple[str, list[str] | None]) -> Any:
    kind, choices = spec

    if value is None or (isinstance(value, str) and not value.strip()):
        return None  # clearing a field falls back to the inherited default

    if kind == "float":
        try:
            out = float(str(value).strip())
        except (TypeError, ValueError):
            raise StudioError(f"'{label}' must be a number, got {value!r}")
        if out <= 0:
            raise StudioError(f"'{label}' must be greater than zero")
        return out

    if kind == "int":
        try:
            out = int(str(value).strip())
        except (TypeError, ValueError):
            raise StudioError(f"'{label}' must be a whole number, got {value!r}")
        if out < 0:
            raise StudioError(f"'{label}' cannot be negative")
        return out

    out = str(value).strip()
    if choices and out not in choices:
        raise StudioError(f"'{label}' must be one of {', '.join(choices)} — got {out!r}")
    return out


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    A half-written workflow.yaml is read on the next poll tick; the orchestrator
    re-parses config on every tick, so a torn write is a live failure.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
