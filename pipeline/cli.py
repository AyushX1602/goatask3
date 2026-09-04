"""CLI entrypoint. See design.md 7 for the full command surface.

Commands are added phase by phase; this stub exists so `python -m pipeline`
resolves from Phase 0 onward and every later phase has a place to register
its subcommand.
"""

from __future__ import annotations

import typer
from rich.console import Console

from pipeline import __version__
from pipeline.config import ensure_dirs, get_config

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


@app.callback()
def _main() -> None:
    ensure_dirs()


@app.command()
def version() -> None:
    """Print the pipeline version and a redacted view of the active config."""
    console.print(f"face-chain-verify v{__version__}")
    console.print(get_config())


if __name__ == "__main__":
    app()
