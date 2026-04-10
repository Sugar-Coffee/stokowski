"""CLI entry point for Stokowski."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import select
import signal
import sys
import termios
import threading
import tty
from pathlib import Path


def _load_dotenv():
    """Load .env files from cwd and .stokowski/ if they exist."""
    candidates = [Path(".env"), Path(".stokowski/.env")]
    for env_file in candidates:
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .orchestrator import Orchestrator

console = Console()

# Module-level update message, set once at startup
_update_message: str | None = None


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


# ── Update check ───────────────────────────────────────────────────────────

async def check_for_updates():
    """Check if a newer Stokowski release is available on GitHub."""
    global _update_message
    from . import __version__

    def _parse_ver(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0,)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.github.com/repos/erikpr1994/stokowski/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code != 200:
                return
            latest_tag = resp.json().get("tag_name", "").lstrip("v")
            if not latest_tag:
                return
            if _parse_ver(latest_tag) > _parse_ver(__version__):
                _update_message = (
                    f"Stokowski {latest_tag} available (you have {__version__})"
                )
    except Exception:
        pass  # Update checks are best-effort


# ── Keyboard handler ────────────────────────────────────────────────────────

HELP_TEXT = """
[bold white]Stokowski keyboard shortcuts[/bold white]

  [bold yellow]q[/bold yellow]   Quit — graceful shutdown, kills all agents
  [bold yellow]s[/bold yellow]   Status — show running agents and token usage
  [bold yellow]h[/bold yellow]   Help — show this message
  [bold yellow]r[/bold yellow]   Refresh — force an immediate Linear poll
"""


def print_status(orch: Orchestrator):
    snap = orch.get_state_snapshot()
    running  = snap["counts"]["running"]
    retrying = snap["counts"]["retrying"]
    total_tok = snap["totals"]["total_tokens"]
    secs = snap["totals"]["seconds_running"]

    table = Table(box=None, padding=(0, 2), show_header=True, header_style="dim")
    table.add_column("Issue",  style="cyan",  width=12)
    table.add_column("Status", style="green", width=12)
    table.add_column("Turns",  justify="right", width=6)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Last activity", style="dim")

    for r in snap["running"]:
        table.add_row(
            r["issue_identifier"],
            r["status"],
            str(r["turn_count"]),
            f"{r['tokens']['total_tokens']:,}",
            r["last_message"][:60] if r["last_message"] else "—",
        )
    for r in snap["retrying"]:
        table.add_row(
            r["issue_identifier"],
            f"[blue]retry #{r['attempt']}[/blue]",
            "—", "—",
            r["error"] or "waiting",
        )
    if not snap["running"] and not snap["retrying"]:
        table.add_row("—", "idle", "—", "—", "no active agents")

    console.print()
    console.print(Panel(
        table,
        title=f"[bold]Stokowski Status[/bold]  "
              f"[dim]running={running}  retrying={retrying}  "
              f"tokens={total_tok:,}  uptime={secs:.0f}s[/dim]",
        border_style="yellow",
    ))
    console.print()


class KeyboardHandler:
    """Reads single keypresses from stdin in a background thread."""

    def __init__(self, orch: Orchestrator, loop: asyncio.AbstractEventLoop):
        self._orch = orch
        self._loop = loop
        self._stop = threading.Event()

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        if not sys.stdin.isatty():
            return

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                # Non-blocking check every 100ms
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not ready:
                    continue
                ch = sys.stdin.read(1).lower()
                self._handle(ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _handle(self, ch: str):
        if ch == "q":
            console.print("\n[yellow]Shutting down...[/yellow]")
            asyncio.run_coroutine_threadsafe(self._orch.stop(), self._loop)
            self._stop.set()
        elif ch == "s":
            print_status(self._orch)
        elif ch == "h":
            console.print(HELP_TEXT)
        elif ch == "r":
            console.print("[dim]Forcing poll...[/dim]")
            if hasattr(self._orch, '_stop_event'):
                # Wake the poll loop early
                self._loop.call_soon_threadsafe(
                    lambda: self._loop.create_task(self._orch._tick())
                )

    def stop(self):
        self._stop.set()


# ── Main orchestrator runner ─────────────────────────────────────────────────

def _make_footer(orch: Orchestrator) -> Text:
    """Build the persistent footer line."""
    try:
        snap = orch.get_state_snapshot()
        running = snap["counts"]["running"]
        retrying = snap["counts"]["retrying"]
        tokens = snap["totals"]["total_tokens"]
        if running:
            status = f"[green]●[/green] {running} running"
        elif retrying:
            status = f"[blue]●[/blue] {retrying} retrying"
        else:
            status = "[dim]● idle[/dim]"
        meta = f"  [dim]tokens={tokens:,}[/dim]" if tokens else ""
    except Exception:
        status = "[dim]● idle[/dim]"
        meta = ""

    update = f"  [dim yellow]⬆ {_update_message}[/dim yellow]" if _update_message else ""

    return Text.from_markup(
        f"  [bold yellow]q[/bold yellow] quit  "
        f"[bold yellow]s[/bold yellow] status  "
        f"[bold yellow]r[/bold yellow] refresh  "
        f"[bold yellow]h[/bold yellow] help"
        f"     {status}{meta}{update}"
    )


async def run_orchestrator(workflow_path: str, port: int | None = None):
    orch = Orchestrator(workflow_path)
    loop = asyncio.get_running_loop()

    # Start keyboard handler
    kb = KeyboardHandler(orch, loop)
    kb.start()

    # Optional web server
    _uvicorn_server = None
    _uvicorn_task = None
    if port is not None:
        try:
            from .web import create_app
            import uvicorn

            app = create_app(orch)
            server_config = uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="warning",
            )
            _uvicorn_server = uvicorn.Server(server_config)
            _uvicorn_server.install_signal_handlers = lambda: None
            _uvicorn_task = asyncio.create_task(_uvicorn_server.serve())
            console.print(f"[green]Web dashboard →[/green] http://127.0.0.1:{port}")
        except ImportError:
            console.print(
                "[yellow]Install web extras for dashboard: pip install stokowski[web][/yellow]"
            )

    await check_for_updates()

    console.print(Panel(
        f"[bold]Stokowski[/bold]  [dim]Claude Code Orchestrator[/dim]\n"
        f"[dim]workflow:[/dim] {workflow_path}",
        border_style="dim",
    ))

    async def _update_footer(live: Live):
        while True:
            try:
                live.update(_make_footer(orch))
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    with Live(_make_footer(orch), console=console, refresh_per_second=2) as live:
        footer_task = asyncio.create_task(_update_footer(live))
        try:
            await orch.start()
        finally:
            footer_task.cancel()
            kb.stop()
            if _uvicorn_server is not None:
                _uvicorn_server.should_exit = True
                if _uvicorn_task is not None:
                    try:
                        await asyncio.wait_for(_uvicorn_task, timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
            _force_kill_children()
            console.print("[green]All agents stopped.[/green]")


async def run_manager(root_config_path: str, port: int | None = None):
    """Run multiple workflows from a root config."""
    from .config import parse_root_config
    from .manager import Manager

    root_cfg = parse_root_config(root_config_path)
    mgr = Manager(
        root_cfg.workflow_paths,
        shared_raw=root_cfg.shared_raw,
        workflow_enabled=root_cfg.workflow_enabled,
    )
    loop = asyncio.get_running_loop()

    # Read port from root config if not specified on CLI
    if port is None:
        server_raw = root_cfg.shared_raw.get("server", {})
        if isinstance(server_raw, dict) and server_raw.get("port"):
            port = int(server_raw["port"])

    # Keyboard handler — uses manager
    kb = _ManagerKeyboardHandler(mgr, loop)
    kb.start()

    # Optional web server
    _uvicorn_server = None
    _uvicorn_task = None
    if port is not None:
        try:
            from .web import create_app_multi
            import uvicorn

            app = create_app_multi(mgr)
            server_config = uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="warning",
            )
            _uvicorn_server = uvicorn.Server(server_config)
            _uvicorn_server.install_signal_handlers = lambda: None
            _uvicorn_task = asyncio.create_task(_uvicorn_server.serve())
            console.print(f"[green]Web dashboard →[/green] http://127.0.0.1:{port}")
        except ImportError:
            console.print(
                "[yellow]Install web extras for dashboard: pip install stokowski[web][/yellow]"
            )

    await check_for_updates()

    names = ", ".join(root_cfg.workflow_paths.keys())
    console.print(Panel(
        f"[bold]Stokowski[/bold]  [dim]Claude Code Orchestrator (multi-workflow)[/dim]\n"
        f"[dim]workflows:[/dim] {names}",
        border_style="dim",
    ))

    async def _update_footer(live: Live):
        while True:
            try:
                live.update(_make_manager_footer(mgr))
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    with Live(_make_manager_footer(mgr), console=console, refresh_per_second=2) as live:
        footer_task = asyncio.create_task(_update_footer(live))
        try:
            await mgr.start()
        finally:
            footer_task.cancel()
            kb.stop()
            if _uvicorn_server is not None:
                _uvicorn_server.should_exit = True
                if _uvicorn_task is not None:
                    try:
                        await asyncio.wait_for(_uvicorn_task, timeout=2.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
            _force_kill_children()
            console.print("[green]All agents stopped.[/green]")


def _make_manager_footer(mgr) -> Text:
    """Build footer for multi-workflow mode."""
    try:
        snap = mgr.get_aggregate_snapshot()
        running = snap["counts"]["running"]
        retrying = snap["counts"]["retrying"]
        tokens = snap["totals"]["total_tokens"]
        n_wf = len(mgr.orchestrators)
        if running:
            status = f"[green]●[/green] {running} running ({n_wf} workflows)"
        elif retrying:
            status = f"[blue]●[/blue] {retrying} retrying ({n_wf} workflows)"
        else:
            status = f"[dim]● idle ({n_wf} workflows)[/dim]"
        meta = f"  [dim]tokens={tokens:,}[/dim]" if tokens else ""
    except Exception:
        status = "[dim]● idle[/dim]"
        meta = ""

    update = f"  [dim yellow]⬆ {_update_message}[/dim yellow]" if _update_message else ""

    return Text.from_markup(
        f"  [bold yellow]q[/bold yellow] quit  "
        f"[bold yellow]s[/bold yellow] status  "
        f"[bold yellow]r[/bold yellow] refresh  "
        f"[bold yellow]h[/bold yellow] help"
        f"     {status}{meta}{update}"
    )


class _ManagerKeyboardHandler:
    """Keyboard handler for multi-workflow mode."""

    def __init__(self, manager, loop):
        self._manager = manager
        self._loop = loop
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        import select
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    self._handle(ch)
        except Exception:
            pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _handle(self, ch: str):
        if ch == "q":
            console.print("\n[yellow]Shutting down all workflows...[/yellow]")
            asyncio.run_coroutine_threadsafe(self._manager.stop(), self._loop)
        elif ch == "s":
            snap = self._manager.get_aggregate_snapshot()
            running = snap["counts"]["running"]
            retrying = snap["counts"]["retrying"]
            gates = snap["counts"]["gates"]
            tokens = snap["totals"]["total_tokens"]
            console.print(
                f"\n[bold]Status:[/bold] {running} running, "
                f"{retrying} retrying, {gates} gates, "
                f"{tokens:,} tokens"
            )
            for name, wf_snap in snap.get("workflows", {}).items():
                wr = wf_snap.get("counts", {}).get("running", 0)
                wt = wf_snap.get("totals", {}).get("total_tokens", 0)
                console.print(f"  [dim]{name}:[/dim] {wr} running, {wt:,} tokens")
        elif ch == "r":
            console.print("[dim]Forcing poll on all workflows...[/dim]")
            for orch in self._manager.orchestrators.values():
                asyncio.run_coroutine_threadsafe(orch._tick(), self._loop)
        elif ch == "h":
            console.print(
                "\n[bold]Keys:[/bold] "
                "[yellow]q[/yellow] quit  "
                "[yellow]s[/yellow] status  "
                "[yellow]r[/yellow] refresh  "
                "[yellow]h[/yellow] help"
            )

# ── Init ──────────────────────────────────────────────────────────────────────

_INIT_TEMPLATE = """\
# Stokowski workflow config
# Docs: https://github.com/erikpr1994/stokowski

tracker:
  kind: {tracker_kind}
{tracker_fields}

{states_section}

polling:
  interval_ms: 15000

workspace:
  mode: worktree
  repo_path: {repo_path}
  root: {repo_path}/.worktrees

hooks:
  after_create: |
    # Install dependencies after creating a new worktree
    # npm install || pnpm install --frozen-lockfile || true
  before_run: |
    git fetch origin main 2>/dev/null
    git rebase origin/main 2>/dev/null || git rebase --abort 2>/dev/null || true
  timeout_ms: 120000

claude:
  permission_mode: auto
  max_turns: 20
  turn_timeout_ms: 3600000
  stall_timeout_ms: 300000

agent:
  max_concurrent_agents: 3
  max_retry_backoff_ms: 300000

prompts:
  global_prompt: prompts/global.md

server:
  port: 4200

states:
  implement:
    type: agent
    prompt: prompts/implement.md
    linear_state: active
    max_turns: 30
    session: inherit
    transitions:
      complete: done

  done:
    type: terminal
    linear_state: terminal
"""


def _run_init():
    """Interactive init command to scaffold a workflow config."""
    import shutil
    import subprocess

    console.print("[bold]Stokowski Init[/bold]\n")

    # Check required tools
    checks = [
        ("claude", "Claude Code CLI"),
        ("gh", "GitHub CLI"),
        ("git", "Git"),
    ]
    optional_checks = [
        ("codex", "Codex CLI"),
        ("gemini", "Gemini CLI"),
    ]

    all_ok = True
    for cmd, label in checks:
        if shutil.which(cmd):
            console.print(f"  [green]OK[/green] {label} ({cmd})")
        else:
            console.print(f"  [red]MISSING[/red] {label} ({cmd})")
            all_ok = False

    for cmd, label in optional_checks:
        if shutil.which(cmd):
            console.print(f"  [green]OK[/green] {label} ({cmd}) [dim](optional)[/dim]")
        else:
            console.print(f"  [dim]--[/dim] {label} ({cmd}) [dim](optional)[/dim]")

    if not all_ok:
        console.print("\n[red]Install missing required tools before continuing.[/red]")
        sys.exit(1)

    console.print()

    # Repo path
    repo_path = os.getcwd()
    out_dir = Path(repo_path) / ".stokowski"

    # Check if tracker is already configured
    import yaml as _yaml
    tracker_kind = ""
    tracker_fields = ""
    states_section = ""
    env_key_name = ""
    env_key_value = ""

    root_config_path = out_dir / "stokowski.yaml"
    if root_config_path.exists():
        try:
            root_raw = _yaml.safe_load(root_config_path.read_text()) or {}
            existing_tracker = root_raw.get("tracker", {})
            if isinstance(existing_tracker, dict) and existing_tracker.get("kind"):
                tracker_kind = existing_tracker["kind"]
                env_key_name = "GITHUB_TOKEN" if tracker_kind == "github" else "LINEAR_API_KEY"
                console.print(f"[dim]Tracker already configured: {tracker_kind}[/dim]")
        except Exception:
            pass

    if not tracker_kind:
        console.print("[bold]Tracker:[/bold]")
        console.print("  1. Linear")
        console.print("  2. GitHub Issues")
        choice = input("\nSelect tracker [1]: ").strip() or "1"

        if choice == "2":
            tracker_kind = "github"
            owner = input("GitHub owner (org or user): ").strip()
            repo = input("GitHub repo name: ").strip()
            tracker_fields = f"  github_owner: {owner}\n  github_repo: {repo}\n  github_token: $GITHUB_TOKEN"
            states_section = f"github_states:\n  todo: \"Todo\"\n  active: \"In Progress\"\n  blocked: \"Blocked\"\n  terminal:\n    - Done"
            env_key_name = "GITHUB_TOKEN"
            env_key_value = input("GitHub token (or press Enter to set later): ").strip()
        else:
            tracker_kind = "linear"
            team_key = input("Linear team key (e.g. DEV): ").strip() or "DEV"
            tracker_fields = f"  team_key: \"{team_key}\"\n  api_key: $LINEAR_API_KEY"
            states_section = f"linear_states:\n  todo: \"Todo\"\n  active: \"In Progress\"\n  review: \"Human Review\"\n  gate_approved: \"Gate Approved\"\n  rework: \"Rework\"\n  blocked: \"Blocked\"\n  terminal:\n    - Done\n    - Canceled"
            env_key_name = "LINEAR_API_KEY"
            env_key_value = input("Linear API key (or press Enter to set later): ").strip()

    # Webhook configuration
    webhook_secret = ""
    webhook_configured = False
    if root_config_path.exists():
        try:
            root_raw = _yaml.safe_load(root_config_path.read_text()) or {}
            existing_wh = root_raw.get("webhook", {})
            if isinstance(existing_wh, dict) and existing_wh.get("secret"):
                webhook_configured = True
                console.print(f"[dim]Webhook already configured[/dim]")
        except Exception:
            pass

    if not webhook_configured:
        console.print("\n[bold]Event delivery:[/bold]")
        console.print("  1. Polling only [dim](simpler, checks every N seconds)[/dim]")
        console.print("  2. Webhook + polling [dim](instant reactions, polling as fallback)[/dim]")
        wh_choice = input("\nSelect mode [1]: ").strip() or "1"

        if wh_choice == "2":
            webhook_secret = input("Webhook signing secret (or press Enter to generate one): ").strip()
            if not webhook_secret:
                import secrets
                webhook_secret = secrets.token_hex(32)
                console.print(f"  [green]Generated secret:[/green] {webhook_secret}")

            console.print(f"\n  [bold]Setup instructions:[/bold]")
            if tracker_kind == "github":
                console.print(f"  1. Go to your repo → Settings → Webhooks → Add webhook")
                console.print(f"  2. Payload URL: http://<your-host>:4200/api/v1/webhook/github")
                console.print(f"  3. Content type: application/json")
                console.print(f"  4. Secret: {webhook_secret}")
                console.print(f"  5. Events: select 'Issues', 'Pull requests', 'Pull request reviews'")
                console.print(f"  [dim]Docs: https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks[/dim]")
            else:
                console.print(f"  1. Go to Linear → Settings → API → Webhooks → New webhook")
                console.print(f"  2. URL: http://<your-host>:4200/api/v1/webhook/linear")
                console.print(f"  3. Secret: {webhook_secret}")
                console.print(f"  4. Events: select 'Issues' (data change)")
                console.print(f"  [dim]Docs: https://developers.linear.app/docs/graphql/webhooks[/dim]")

            console.print(f"\n  [dim]For local dev, use ngrok or similar to expose the port.[/dim]")

    console.print(f"\n[dim]Repo path:[/dim] {repo_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check if workflows already exist
    existing_workflows = sorted(
        p for p in out_dir.glob("workflow*.yaml")
        if p.name != "stokowski.yaml"
    )

    out_file = out_dir / "workflow.yaml"
    if existing_workflows:
        console.print(f"\n[dim]Found {len(existing_workflows)} existing workflow(s):[/dim]")
        for wf in existing_workflows:
            console.print(f"  [dim]{wf.name}[/dim]")
        console.print(f"[dim]Skipping workflow.yaml creation (use existing workflows)[/dim]")
        out_file = existing_workflows[0]  # reference first for validation
    else:
        if out_file.exists():
            overwrite = input(f"\n{out_file} already exists. Overwrite? [y/N]: ").strip().lower()
            if overwrite != "y":
                console.print("[yellow]Aborted.[/yellow]")
                return

        # Generate config
        content = _INIT_TEMPLATE.format(
            tracker_kind=tracker_kind,
            tracker_fields=tracker_fields,
            states_section=states_section,
            repo_path=repo_path,
        )

        out_file.write_text(content)
        console.print(f"\n[green]Created {out_file}[/green]")

    # Create prompts directory (only for new projects without existing workflows)
    prompts_dir = out_dir / "prompts"
    if not existing_workflows:
        prompts_dir.mkdir(parents=True, exist_ok=True)

        global_prompt = prompts_dir / "global.md"
        if not global_prompt.exists():
            global_prompt.write_text(
                "# Global Prompt\n\n"
                "You are an autonomous coding agent. Work autonomously — do NOT use\n"
                "AskUserQuestion or pause for input. Read CLAUDE.md for project\n"
                "conventions before starting.\n"
            )
            console.print(f"[green]Created {global_prompt}[/green]")

        implement_prompt = prompts_dir / "implement.md"
        if not implement_prompt.exists():
            implement_prompt.write_text(
                "# Implement\n\n"
                "Implement the changes described in the issue. Follow the project's\n"
                "coding conventions. Write tests if applicable. Create a PR when done.\n"
            )
            console.print(f"[green]Created {implement_prompt}[/green]")

    # ── Repair pass: ensure all files exist and have correct values ──
    console.print("\n[bold]Checking project files...[/bold]")

    # .env — ensure key exists and has a value
    env_file = out_dir / ".env"
    if env_file.exists():
        existing_env = env_file.read_text()
        has_key = False
        for line in existing_env.splitlines():
            if line.strip().startswith(env_key_name + "="):
                val = line.split("=", 1)[1].strip()
                if val:
                    has_key = True
                break
        if not has_key:
            if not env_key_value:
                env_key_value = input(
                    f"  {env_key_name} not set in {env_file}. Enter value (or Enter to skip): "
                ).strip()
            if env_key_value:
                updated = False
                lines = existing_env.splitlines(keepends=True)
                for i, line in enumerate(lines):
                    if line.strip().startswith(env_key_name + "="):
                        lines[i] = f"{env_key_name}={env_key_value}\n"
                        updated = True
                        break
                if not updated:
                    lines.append(f"{env_key_name}={env_key_value}\n")
                env_file.write_text("".join(lines))
                console.print(f"  [green]Updated {env_file} with {env_key_name}[/green]")
            else:
                console.print(f"  [yellow]{env_key_name} empty in {env_file}[/yellow]")
        else:
            console.print(f"  [green]{env_file}: {env_key_name} set[/green]")
    else:
        env_content = f"# Stokowski environment variables\n{env_key_name}={env_key_value}\n"
        env_file.write_text(env_content)
        console.print(f"  [green]Created {env_file}[/green]")

    # .gitignore — ensure required entries exist
    gitignore = Path(repo_path) / ".gitignore"
    required_ignores = [".stokowski/.env", ".stokowski/.stokowski_state_*.json", ".worktrees/"]
    if gitignore.exists():
        existing_gi = gitignore.read_text()
        missing = [e for e in required_ignores if e not in existing_gi]
        if missing:
            gitignore.write_text(
                existing_gi.rstrip() + "\n\n# Stokowski\n" + "\n".join(missing) + "\n"
            )
            console.print(f"  [green]Updated .gitignore (+{len(missing)} entries)[/green]")
        else:
            console.print(f"  [green].gitignore: all entries present[/green]")
    else:
        gitignore.write_text("# Stokowski\n" + "\n".join(required_ignores) + "\n")
        console.print(f"  [green]Created .gitignore[/green]")

    # stokowski.yaml — ensure shared sections exist
    import yaml as _yaml
    root_config = out_dir / "stokowski.yaml"
    if root_config.exists():
        root_raw = _yaml.safe_load(root_config.read_text()) or {}
        missing_sections = []
        defaults = {
            "tracker": {"kind": tracker_kind},
            "workspace": {"mode": "worktree", "repo_path": repo_path, "root": f"{repo_path}/.worktrees"},
            "claude": {"permission_mode": "auto", "max_turns": 20},
            "agent": {"max_concurrent_agents": 3},
            "server": {"port": 4200},
        }
        for section, default_val in defaults.items():
            if section not in root_raw or not root_raw[section]:
                missing_sections.append(section)
                root_raw[section] = default_val
        if missing_sections:
            # Re-write with added sections (append to preserve comments)
            additions = "\n# Added by stokowski init\n"
            for section in missing_sections:
                additions += f"{section}:\n"
                for k, v in defaults[section].items():
                    additions += f"  {k}: {v}\n"
                additions += "\n"
            root_config.write_text(root_config.read_text().rstrip() + "\n" + additions)
            console.print(f"  [green]Updated stokowski.yaml (+{', '.join(missing_sections)})[/green]")
        else:
            console.print(f"  [green]stokowski.yaml: all shared sections present[/green]")

    # Load env so validation works
    if env_key_value:
        os.environ.setdefault(env_key_name, env_key_value)

    # Validate all workflows (with shared config merged in)
    console.print("\n[bold]Validating workflows...[/bold]")
    from .config import parse_workflow_file, validate_config
    shared_raw = None
    if root_config.exists():
        try:
            shared_raw = {
                k: v for k, v in (_yaml.safe_load(root_config.read_text()) or {}).items()
                if k in {"tracker", "linear_states", "github_states", "workspace",
                         "hooks", "claude", "agent", "server", "webhook"} and v
            }
        except Exception:
            pass

    # Deprecated fields that should live in root config, not per-workflow
    _SHARED_KEYS = {"tracker", "linear_states", "github_states", "workspace",
                    "hooks", "claude", "agent", "server", "webhook"}

    # Auto-migrate old schedule format (title/description → create_command)
    all_wf_files = existing_workflows if existing_workflows else [out_file]
    for wf_path in all_wf_files:
        try:
            wf_raw = _yaml.safe_load(wf_path.read_text()) or {}
            sched = wf_raw.get("schedule")
            if isinstance(sched, dict) and "title" in sched and "create_command" not in sched:
                # Old format: convert title/description/labels to create_command
                old_title = sched.get("title", "")
                old_desc = sched.get("description", "").strip()
                old_labels = sched.get("labels", [])
                old_priority = sched.get("priority", 3)

                # Build a create_command using the tracker's CLI
                tracker_raw = wf_raw.get("tracker", shared_raw.get("tracker", {}) if shared_raw else {})
                kind = tracker_raw.get("kind", "linear") if isinstance(tracker_raw, dict) else "linear"

                if kind == "github":
                    label_flags = " ".join(f"--label {l}" for l in old_labels) if old_labels else ""
                    cmd = f'gh issue create --title "{old_title}" {label_flags}'.strip()
                    if old_desc:
                        cmd += f' --body "{old_desc[:100]}"'
                else:
                    # Linear CLI or generic
                    cmd = f'# TODO: replace with your tracker CLI command\n# Old title: {old_title}'

                wf_raw["schedule"] = {
                    "cron": sched.get("cron", ""),
                    "create_command": cmd,
                }
                wf_path.write_text(_yaml.dump(wf_raw, default_flow_style=False, sort_keys=False))
                console.print(f"  [green]{wf_path.name}: migrated schedule to create_command format[/green]")
        except Exception as e:
            console.print(f"  [yellow]{wf_path.name}: schedule migration failed: {e}[/yellow]")

    workflows_to_validate = all_wf_files
    for wf_path in workflows_to_validate:
        try:
            wf = parse_workflow_file(str(wf_path), shared_raw=shared_raw)
            errors = validate_config(wf.config)
            if errors:
                console.print(f"  [yellow]{wf_path.name}:[/yellow]")
                for e in errors:
                    console.print(f"    [yellow]- {e}[/yellow]")
            else:
                console.print(f"  [green]{wf_path.name}: valid[/green]")

            # Auto-migrate shared fields from workflow to root config
            if root_config.exists():
                wf_raw = _yaml.safe_load(wf_path.read_text()) or {}
                duplicated = [k for k in _SHARED_KEYS if k in wf_raw and wf_raw[k]]
                if duplicated:
                    # Read root config and add missing shared sections
                    root_raw = _yaml.safe_load(root_config.read_text()) or {}
                    additions = ""
                    for key in duplicated:
                        if key not in root_raw or not root_raw[key]:
                            # Move to root config
                            additions += f"\n# Migrated from {wf_path.name}\n"
                            additions += _yaml.dump({key: wf_raw[key]}, default_flow_style=False)
                    if additions:
                        root_config.write_text(root_config.read_text().rstrip() + "\n" + additions)

                    # Remove shared sections from workflow file
                    wf_content = wf_path.read_text()
                    cleaned_raw = {k: v for k, v in wf_raw.items() if k not in _SHARED_KEYS}
                    wf_path.write_text(_yaml.dump(cleaned_raw, default_flow_style=False, sort_keys=False))
                    console.print(
                        f"  [green]{wf_path.name}: migrated {', '.join(duplicated)} "
                        f"to stokowski.yaml[/green]"
                    )
        except Exception as e:
            console.print(f"  [red]{wf_path.name}: {e}[/red]")

    # Create or update root config (stokowski.yaml)
    # Create or update root config (stokowski.yaml) with shared settings
    root_config = out_dir / "stokowski.yaml"
    if not root_config.exists():
        workflow_files = sorted(
            p for p in out_dir.glob("workflow*.yaml")
            if p.name != "stokowski.yaml"
        )
        if not workflow_files:
            workflow_files = [out_file]

        workflows_block = "workflows:\n"
        for wf_file in workflow_files:
            name = wf_file.stem.replace("workflow-", "").replace("workflow", "default")
            workflows_block += f"  {name}:\n    path: ./{wf_file.name}\n"

        root_content = (
            f"# Stokowski root config — shared settings + workflow list\n"
            f"# Run: stokowski {root_config}\n"
            f"#\n"
            f"# Shared settings below apply to ALL workflows.\n"
            f"# Individual workflows can override any section.\n\n"
            f"# ── Shared config (all workflows inherit these) ──\n\n"
            f"tracker:\n"
            f"  kind: {tracker_kind}\n"
            f"{tracker_fields}\n\n"
            f"{states_section}\n\n"
            f"workspace:\n"
            f"  mode: worktree\n"
            f"  repo_path: {repo_path}\n"
            f"  root: {repo_path}/.worktrees\n\n"
            f"hooks:\n"
            f"  after_create: |\n"
            f"    # Install dependencies after creating a new worktree\n"
            f"    # npm install || pnpm install --frozen-lockfile || true\n"
            f"  before_run: |\n"
            f"    git fetch origin main 2>/dev/null\n"
            f"    git rebase origin/main 2>/dev/null || git rebase --abort 2>/dev/null || true\n"
            f"  timeout_ms: 120000\n\n"
            f"claude:\n"
            f"  permission_mode: auto\n"
            f"  max_turns: 20\n"
            f"  turn_timeout_ms: 3600000\n"
            f"  stall_timeout_ms: 300000\n\n"
            f"agent:\n"
            f"  max_concurrent_agents: 3\n"
            f"  max_retry_backoff_ms: 300000\n\n"
            f"server:\n"
            f"  port: 4200\n\n"
            + (f"webhook:\n  secret: {webhook_secret}\n\n" if webhook_secret else "")
            + f"# ── Workflows ──\n\n"
            f"{workflows_block}"
        )
        root_config.write_text(root_content)
        console.print(f"[green]Created {root_config} ({len(workflow_files)} workflow(s))[/green]")
    else:
        existing_root = root_config.read_text()
        if out_file.name not in existing_root:
            console.print(f"[yellow]Note: {out_file.name} not listed in {root_config} — add it manually[/yellow]")
        else:
            console.print(f"[dim]Root config already includes {out_file.name}[/dim]")

    console.print(f"\n[bold]Next steps:[/bold]")
    step = 1
    if not env_key_value:
        console.print(f"  {step}. Set your API key in {env_file}")
        step += 1
    console.print(f"  {step}. Edit prompts in {prompts_dir}/")
    console.print(f"  {step + 1}. Run:     stokowski          [dim](auto-detects .stokowski/stokowski.yaml)[/dim]")
    console.print(f"  {step + 2}. Dry-run: stokowski --dry-run {out_file}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(
        description="Stokowski - Orchestrate Claude Code agents from Linear issues"
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="Path to workflow.yaml or WORKFLOW.md (auto-detected if not specified)",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="Enable web dashboard on this port",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate config and show candidates without dispatching",
    )

    args = parser.parse_args()

    # Handle "stokowski init" as a special case
    if args.workflow == "init":
        _load_dotenv()
        _run_init()
        return

    if args.workflow is None:
        # Auto-detect: root config first, then single workflow
        if Path(".stokowski/stokowski.yaml").exists():
            args.workflow = ".stokowski/stokowski.yaml"
        elif Path(".stokowski/stokowski.yml").exists():
            args.workflow = ".stokowski/stokowski.yml"
        elif Path("workflow.yaml").exists():
            args.workflow = "./workflow.yaml"
        elif Path("workflow.yml").exists():
            args.workflow = "./workflow.yml"
        elif Path("WORKFLOW.md").exists():
            args.workflow = "./WORKFLOW.md"
        else:
            console.print(
                "[red]No config found. Run 'stokowski init' to set up, "
                "or specify a path: stokowski <path>[/red]"
            )
            sys.exit(1)

    _load_dotenv()
    setup_logging(args.verbose)

    from .config import is_root_config

    if args.dry_run:
        asyncio.run(dry_run(args.workflow))
    elif is_root_config(args.workflow):
        try:
            asyncio.run(run_manager(args.workflow, args.port))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — killing all agents...[/yellow]")
            _force_kill_children()
            console.print("[green]Done.[/green]")
    else:
        try:
            asyncio.run(run_orchestrator(args.workflow, args.port))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted — killing all agents...[/yellow]")
            _force_kill_children()
            console.print("[green]Done.[/green]")


def _force_kill_children():
    """Kill any lingering claude -p processes."""
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude.*-p.*--output-format.*stream-json"],
            capture_output=True, text=True,
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    pid = int(pid_str.strip())
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        os.kill(pid, signal.SIGKILL)
                except (ValueError, ProcessLookupError, PermissionError, OSError):
                    pass
    except Exception:
        pass


# ── Dry run ───────────────────────────────────────────────────────────────────

async def dry_run(workflow_path: str):
    from .config import parse_workflow_file, validate_config

    console.print("[bold]Dry run mode[/bold]\n")

    try:
        workflow = parse_workflow_file(workflow_path)
    except Exception as e:
        console.print(f"[red]Failed to load workflow: {e}[/red]")
        sys.exit(1)

    errors = validate_config(workflow.config)
    if errors:
        for e in errors:
            console.print(f"[red]Config error: {e}[/red]")
        sys.exit(1)

    cfg = workflow.config
    console.print("[green]Config valid[/green]")
    console.print(f"  Tracker: {cfg.tracker.kind}")
    console.print(f"  Project: {cfg.tracker.project_slug}")
    console.print(f"  Max agents: {cfg.agent.max_concurrent_agents}")
    console.print(f"  Claude model: {cfg.claude.model or 'default'}")
    console.print(f"  Permission mode: {cfg.claude.permission_mode}")
    console.print(f"  Workspace root: {cfg.workspace.resolved_root()}")

    if cfg.schedule:
        console.print(f"\n  [bold]Schedule[/bold]:")
        console.print(f"    Cron: {cfg.schedule.cron}")
        console.print(f"    Command: {cfg.schedule.create_command.strip()[:80]}")

    if cfg.states:
        console.print(f"\n  [bold]State machine[/bold] ({len(cfg.states)} states):")
        console.print(f"    Entry state: {cfg.entry_state}")
        sc = cfg._states_cfg
        console.print(f"    States: active={sc.active}, review={sc.review}")
        for name, state in cfg.states.items():
            transitions = ", ".join(f"{k}->{v}" for k, v in state.transitions.items())
            console.print(f"    {name} ({state.type}) -> {transitions or 'terminal'}")
    else:
        console.print(f"\n  [dim]Legacy mode (no state machine)[/dim]")

    console.print()

    if cfg.tracker.kind == "github":
        from .github_issues import GitHubIssuesClient
        client = GitHubIssuesClient(
            owner=cfg.tracker.github_owner,
            repo=cfg.tracker.github_repo,
            token=cfg.resolved_api_key(),
        )
    else:
        from .linear import LinearClient
        client = LinearClient(
            endpoint=cfg.tracker.endpoint,
            api_key=cfg.resolved_api_key(),
        )

    try:
        candidates = await client.fetch_candidate_issues(
            cfg.tracker.project_slug,
            cfg.active_linear_states(),
        )
    except Exception as e:
        console.print(f"[red]Failed to fetch candidates: {e}[/red]")
        await client.close()
        sys.exit(1)

    console.print(f"[bold]Found {len(candidates)} candidate issues:[/bold]\n")

    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("State", style="green")
    table.add_column("Priority")
    table.add_column("Title")
    table.add_column("Labels", style="dim")

    for issue in candidates:
        table.add_row(
            issue.identifier,
            issue.state,
            str(issue.priority or "—"),
            issue.title[:60],
            ", ".join(issue.labels) if issue.labels else "",
        )

    console.print(table)
    await client.close()


if __name__ == "__main__":
    cli()
