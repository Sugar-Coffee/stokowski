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
    """Check if the local Stokowski repo is behind origin/main."""
    global _update_message
    logger = logging.getLogger("stokowski")
    repo_dir = Path(__file__).resolve().parent.parent

    async def _git(*args: str) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return proc.returncode, stdout.decode().strip()

    # Check if it's a git repo
    rc, _ = await _git("rev-parse", "--git-dir")
    if rc != 0:
        logger.info("Stokowski not installed from git clone — update checks disabled")
        return

    # Fetch latest from remote (quiet, fast)
    rc, _ = await _git("fetch", "--quiet")
    if rc != 0:
        return

    # Count commits behind origin/main
    rc, count_str = await _git("rev-list", "--count", "HEAD..origin/main")
    if rc != 0 or not count_str:
        return

    try:
        behind = int(count_str)
    except ValueError:
        return

    if behind > 0:
        _update_message = f"Stokowski update available ({behind} commit{'s' if behind != 1 else ''} behind)"
        logger.info(f"Update available: {_update_message}")


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


# ── CLI ───────────────────────────────────────────────────────────────────────

def cli():
    parser = argparse.ArgumentParser(
        description="Stokowski - Orchestrate Claude Code agents from Linear issues"
    )
    parser.add_argument(
        "workflow",
        nargs="?",
        default="./WORKFLOW.md",
        help="Path to WORKFLOW.md (default: ./WORKFLOW.md)",
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

    console.print("[green]Config valid[/green]")
    console.print(f"  Tracker: {workflow.config.tracker.kind}")
    console.print(f"  Project: {workflow.config.tracker.project_slug}")
    console.print(f"  Active states: {workflow.config.tracker.active_states}")
    console.print(f"  Max agents: {workflow.config.agent.max_concurrent_agents}")
    console.print(f"  Claude model: {workflow.config.claude.model or 'default'}")
    console.print(f"  Permission mode: {workflow.config.claude.permission_mode}")
    console.print(f"  Workspace root: {workflow.config.workspace.resolved_root()}")
    console.print()

    from .linear import LinearClient

    client = LinearClient(
        endpoint=workflow.config.tracker.endpoint,
        api_key=workflow.config.resolved_api_key(),
    )

    try:
        candidates = await client.fetch_candidate_issues(
            workflow.config.tracker.project_slug,
            workflow.config.tracker.active_states,
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
