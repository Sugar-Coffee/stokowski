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
    """Load .env file from cwd if it exists."""
    env_file = Path(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip()


from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .orchestrator import MultiOrchestrator

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
                "https://api.github.com/repos/Sugar-Coffee/stokowski/releases/latest",
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
  [bold yellow]p[/bold yellow]   Pause/resume a project (toggle dispatch for one project)
  [bold yellow]h[/bold yellow]   Help — show this message
  [bold yellow]r[/bold yellow]   Refresh — force an immediate Linear poll
"""


def print_status(orch: MultiOrchestrator):
    snap = orch.get_state_snapshot()
    running  = snap["counts"]["running"]
    retrying = snap["counts"]["retrying"]
    queued   = snap["counts"]["queued"]
    total_tok = snap["totals"]["total_tokens"]
    secs = snap["totals"]["seconds_running"]

    # Per-project summary
    proj_table = Table(box=None, padding=(0, 2), show_header=True, header_style="dim")
    proj_table.add_column("Project", style="cyan")
    proj_table.add_column("Pause", justify="center", width=8)
    proj_table.add_column("Run", justify="right", width=5)
    proj_table.add_column("Gates", justify="right", width=6)
    proj_table.add_column("Queue", justify="right", width=6)
    proj_table.add_column("Tokens", justify="right", width=10)
    for p in snap["projects"]:
        paused = "[red]●[/red]" if p["paused"] else "[green]○[/green]"
        proj_table.add_row(
            p["name"],
            paused,
            str(p["counts"]["running"]),
            str(p["counts"]["gates"]),
            str(p["counts"].get("queued", 0)),
            f"{p['totals']['total_tokens']:,}",
        )

    # Per-issue table
    table = Table(box=None, padding=(0, 2), show_header=True, header_style="dim")
    table.add_column("Project", style="cyan")
    table.add_column("Issue",  style="cyan",  width=12)
    table.add_column("Status", style="green", width=12)
    table.add_column("Turns",  justify="right", width=6)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Last activity", style="dim")

    for r in snap["running"]:
        table.add_row(
            r.get("project_name", "—"),
            r["issue_identifier"],
            r["status"],
            str(r["turn_count"]),
            f"{r['tokens']['total_tokens']:,}",
            r["last_message"][:60] if r["last_message"] else "—",
        )
    for r in snap["retrying"]:
        table.add_row(
            r.get("project_name", "—"),
            r["issue_identifier"],
            f"[blue]retry #{r['attempt']}[/blue]",
            "—", "—",
            r["error"] or "waiting",
        )
    if not snap["running"] and not snap["retrying"]:
        table.add_row("—", "—", "idle", "—", "—", "no active agents")

    console.print()
    console.print(Panel(
        proj_table,
        title=f"[bold]Projects[/bold]  "
              f"[dim]global_cap={snap['pool']['global_cap']}  "
              f"in_use={snap['pool']['global_running']}[/dim]",
        border_style="yellow",
    ))
    console.print(Panel(
        table,
        title=f"[bold]Stokowski Status[/bold]  "
              f"[dim]running={running}  retrying={retrying}  "
              f"queued={queued}  "
              f"tokens={total_tok:,}  uptime={secs:.0f}s[/dim]",
        border_style="yellow",
    ))
    console.print()


def print_pause_menu(orch: MultiOrchestrator):
    """Show numbered list of projects with current pause state."""
    names = orch.project_names
    if not names:
        console.print("[dim]No projects loaded.[/dim]")
        return
    console.print()
    console.print("[bold]Toggle pause for project[/bold] [dim](press number, any other key cancels)[/dim]")
    for i, name in enumerate(names, start=1):
        marker = "[red]paused[/red]" if orch.is_paused(name) else "[green]running[/green]"
        console.print(f"  [bold yellow]{i}[/bold yellow]  {name}  {marker}")
    console.print()


class KeyboardHandler:
    """Reads single keypresses from stdin in a background thread."""

    def __init__(self, orch: MultiOrchestrator, loop: asyncio.AbstractEventLoop):
        self._orch = orch
        self._loop = loop
        self._stop = threading.Event()
        # When non-None, the next keypress is consumed as a pause-menu choice
        # rather than a top-level command.
        self._pause_menu_active: bool = False

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
        if self._pause_menu_active:
            self._pause_menu_active = False
            self._handle_pause_choice(ch)
            return

        if ch == "q":
            console.print("\n[yellow]Shutting down...[/yellow]")
            asyncio.run_coroutine_threadsafe(self._orch.stop(), self._loop)
            self._stop.set()
        elif ch == "s":
            print_status(self._orch)
        elif ch == "p":
            print_pause_menu(self._orch)
            self._pause_menu_active = True
        elif ch == "h":
            console.print(HELP_TEXT)
        elif ch == "r":
            console.print("[dim]Forcing poll on all projects...[/dim]")
            self._loop.call_soon_threadsafe(
                lambda: self._loop.create_task(self._orch.force_tick())
            )

    def _handle_pause_choice(self, ch: str):
        names = self._orch.project_names
        try:
            idx = int(ch) - 1
        except ValueError:
            console.print("[dim]Cancelled.[/dim]")
            return
        if idx < 0 or idx >= len(names):
            console.print("[dim]Cancelled (out of range).[/dim]")
            return
        name = names[idx]
        now_paused = self._orch.toggle(name)
        state = "[red]paused[/red]" if now_paused else "[green]resumed[/green]"
        console.print(f"Project [cyan]{name}[/cyan] is now {state}")

    def stop(self):
        self._stop.set()


# ── Main orchestrator runner ─────────────────────────────────────────────────

def _make_footer(orch: MultiOrchestrator) -> Text:
    """Build the persistent footer line."""
    try:
        snap = orch.get_state_snapshot()
        running = snap["counts"]["running"]
        retrying = snap["counts"]["retrying"]
        queued = snap["counts"].get("queued", 0)
        tokens = snap["totals"]["total_tokens"]
        if running:
            status = f"[green]●[/green] {running} running"
        elif retrying:
            status = f"[blue]●[/blue] {retrying} retrying"
        else:
            status = "[dim]● idle[/dim]"
        # Surface paused projects in the footer so it's obvious at a glance.
        paused = [p["name"] for p in snap.get("projects", []) if p.get("paused")]
        paused_meta = f"  [red]⏸ {','.join(paused)}[/red]" if paused else ""
        queue_meta = f"  [dim]queued={queued}[/dim]" if queued else ""
        token_meta = f"  [dim]tokens={tokens:,}[/dim]" if tokens else ""
        meta = paused_meta + queue_meta + token_meta
    except Exception:
        status = "[dim]● idle[/dim]"
        meta = ""

    update = f"  [dim yellow]⬆ {_update_message}[/dim yellow]" if _update_message else ""

    return Text.from_markup(
        f"  [bold yellow]q[/bold yellow] quit  "
        f"[bold yellow]s[/bold yellow] status  "
        f"[bold yellow]p[/bold yellow] pause  "
        f"[bold yellow]r[/bold yellow] refresh  "
        f"[bold yellow]h[/bold yellow] help"
        f"     {status}{meta}{update}"
    )


async def run_orchestrator(workflow_path: str, port: int | None = None):
    orch = MultiOrchestrator(workflow_path)
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

    if args.workflow is None:
        if Path("workflow.yaml").exists():
            args.workflow = "./workflow.yaml"
        elif Path("workflow.yml").exists():
            args.workflow = "./workflow.yml"
        elif Path("WORKFLOW.md").exists():
            args.workflow = "./WORKFLOW.md"
        else:
            console.print(
                "[red]No workflow file found. Create workflow.yaml or WORKFLOW.md, "
                "or specify a path: stokowski <path>[/red]"
            )
            sys.exit(1)

    _load_dotenv()
    setup_logging(args.verbose)

    if args.dry_run:
        asyncio.run(dry_run(args.workflow))
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
    console.print(f"  Global max_concurrent_agents: {cfg.agent.max_concurrent_agents}")
    console.print(f"  Polling interval: {cfg.polling.interval_ms}ms")
    console.print(f"  Projects: {len(cfg.projects)}")
    console.print()

    from .linear import LinearClient

    for project in cfg.projects:
        per_project_cap = (
            project.max_concurrent
            if project.max_concurrent is not None
            else cfg.agent.max_concurrent_per_project.get(project.name)
        )
        cap_str = f", per-project cap: {per_project_cap}" if per_project_cap else ""
        console.print(f"[bold cyan]Project '{project.name}'[/bold cyan]")
        console.print(f"  Tracker: {project.tracker.kind}  slug={project.tracker.project_slug}{cap_str}")
        console.print(f"  Claude model: {project.claude.model or 'default'}  permission={project.claude.permission_mode}")
        console.print(f"  Workspace root: {project.workspace.resolved_root()}")
        if project.paused:
            console.print(f"  [red]Paused at startup[/red]")

        if project.states:
            console.print(f"  [bold]State machine[/bold] ({len(project.states)} states):")
            console.print(f"    Entry state: {project.entry_state}")
            console.print(
                f"    Linear states: active={project.linear_states.active}, "
                f"review={project.linear_states.review}"
            )
            for name, state in project.states.items():
                transitions = ", ".join(f"{k}->{v}" for k, v in state.transitions.items())
                console.print(f"    {name} ({state.type}) -> {transitions or 'terminal'}")

        client = LinearClient(
            endpoint=project.tracker.endpoint,
            api_key=project.resolved_api_key(),
        )
        try:
            candidates = await client.fetch_candidate_issues(
                project.tracker.project_slug,
                project.active_linear_states(),
            )
        except Exception as e:
            console.print(f"  [red]Failed to fetch candidates: {e}[/red]")
            await client.close()
            continue

        console.print(f"  [bold]{len(candidates)} candidate issue(s):[/bold]")
        if candidates:
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
        console.print()


if __name__ == "__main__":
    cli()
