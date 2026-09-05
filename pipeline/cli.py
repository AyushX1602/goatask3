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


@app.command()
def anchor(run_id: str = typer.Argument(..., help="run id under runs/, must contain evidence.json")) -> None:
    """Anchors an existing run's evidence bundle on the configured EVM
    chain (F8/F9). Requires runs/<run_id>/evidence.json to already exist
    (produced by `run-all` / the web UI's search step on a MATCH)."""
    import json

    from pipeline.chain.evm import EvmClient
    from pipeline.config import RUNS_DIR
    from pipeline.evidence.bundle import EvidenceBundle
    from pipeline.evidence.canonical import evidence_hash_hex

    bundle_path = RUNS_DIR / run_id / "evidence.json"
    if not bundle_path.exists():
        console.print(f"[bold red]no evidence bundle at {bundle_path}[/]")
        raise typer.Exit(1)

    raw = bundle_path.read_bytes()
    data = json.loads(raw)
    bundle = EvidenceBundle(data=data, evidence_hash_hex=evidence_hash_hex(data), canonical_json=raw)

    client = EvmClient()
    console.print(f"anchoring on [bold]{client.chain_name}[/] (chain_id via RPC)...")
    receipt = client.anchor(bundle)

    anchor_record = {
        "tx_hash": receipt.tx_hash,
        "chain_id": receipt.chain_id,
        "block_number": receipt.block_number,
        "contract_address": receipt.contract_address,
        "gas_used": receipt.gas_used,
        "evidence_hash": receipt.evidence_hash_hex,
    }
    (RUNS_DIR / run_id / "anchor.json").write_text(json.dumps(anchor_record, indent=2), encoding="utf-8")

    console.print(f"[bold green]anchored[/] tx={receipt.tx_hash}")
    console.print(f"  chain_id: {receipt.chain_id}")
    console.print(f"  block:    {receipt.block_number}")
    console.print(f"  contract: {receipt.contract_address}")
    console.print(f"  evidence: {receipt.evidence_hash_hex}")


@app.command()
def verify(run_id: str = typer.Argument(..., help="run id under runs/, must contain evidence.json + anchor.json")) -> None:
    """Re-verifies a run's evidence bundle against the on-chain record
    (F9). This is the literal 'demonstrate re-verifying the data against
    the on-chain record' requirement from the brief.

    Exit code 0 only on PASS. Edit one character in evidence.json and
    re-run this to see it report TAMPERED — the required tamper demo."""
    import json

    from pipeline.chain.evm import EvmClient
    from pipeline.chain.reverify import format_report, reverify_bundle
    from pipeline.config import RUNS_DIR

    run_dir = RUNS_DIR / run_id
    bundle_path = run_dir / "evidence.json"
    anchor_path = run_dir / "anchor.json"

    expected_hash = None
    if anchor_path.exists():
        expected_hash = json.loads(anchor_path.read_text(encoding="utf-8"))["evidence_hash"]

    report = reverify_bundle(bundle_path, client=EvmClient(), expected_hash=expected_hash)
    console.print(format_report(report))

    if report.overall != "PASS":
        raise typer.Exit(1)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the local demo UI (architecture.md 5a, prd.md G8/S13).

    Local only, no auth — a visualization layer for the recording, not a
    hosted service. Binds to 127.0.0.1 by default; do not expose this
    beyond localhost.
    """
    import uvicorn

    console.print(f"[bold green]face-chain-verify demo UI[/] -> http://{host}:{port}")
    uvicorn.run("webapp.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
