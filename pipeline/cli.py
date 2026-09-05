"""CLI entrypoint (T2.7 / architecture.md 5a / phases.md).

Structured Exit Codes:
  0: OK / PASS
  1: Verification mismatch / tampered / artifact failure
  2: No face detected or quality gate failure
  3: Provider error / network failure
  4: Search completed with NO_MATCH (no candidate above threshold)
  5: Chain / RPC error
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import typer
from rich.console import Console
from rich.table import Table

from pipeline import __version__
from pipeline.audit.run_log import build_audit, new_run_id, write_audit
from pipeline.chain.evm import EvmClient
from pipeline.chain.reverify import format_report, reverify_bundle
from pipeline.chain.tamper import tamper_run
from pipeline.config import (
    RUNS_DIR,
    ensure_dirs,
    get_commitment_salt,
    get_config,
)
from pipeline.evidence.bundle import EvidenceBundle, build_evidence, detect_image_extension
from pipeline.evidence.canonical import evidence_hash_hex
from pipeline.face.align import align
from pipeline.face.detect import FaceDetector
from pipeline.face.embed import FaceEmbedder
from pipeline.face.quality import passes as quality_passes
from pipeline.face.types import LivenessResult
from pipeline.search.bluesky import BlueskyProvider
from pipeline.search.image_prep import prepare_search_image
from pipeline.search.web_detect import WebDetectProvider
from pipeline.verify.pipeline_run import run_pipeline

EXIT_OK = 0
EXIT_VERIFICATION_MISMATCH = 1
EXIT_NO_FACE = 2
EXIT_PROVIDER_ERROR = 3
EXIT_NO_MATCH = 4
EXIT_CHAIN_ERROR = 5

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
def scan(
    image_path: Path = typer.Argument(..., help="Path to input photograph"),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Explicit run ID (defaults to UTC timestamp)"),
    public_image_url: Optional[str] = typer.Option(None, "--public-image-url", help="Public URL if image is hosted"),
) -> None:
    """Detects, quality-gates, aligns and embeds a probe face.

    Exit code 0 on success; 2 if no face found or quality gate fails.
    """
    if not image_path.exists():
        console.print(f"[bold red]Image file not found:[/] {image_path}")
        raise typer.Exit(EXIT_NO_FACE)

    img = cv2.imread(str(image_path))
    if img is None:
        console.print(f"[bold red]Could not decode image:[/] {image_path}")
        raise typer.Exit(EXIT_NO_FACE)

    detector = FaceDetector()
    faces = detector.detect(img)
    if not faces:
        console.print("[bold red]No face detected in input photograph.[/]")
        raise typer.Exit(EXIT_NO_FACE)

    face = faces[0]
    passes_quality, quality_reason = quality_passes(face)
    if not passes_quality:
        console.print(f"[bold red]Face quality gate failed:[/] {quality_reason}")
        raise typer.Exit(EXIT_NO_FACE)

    embedder = FaceEmbedder()
    crop = align(img, face.kps5)
    embedding = embedder.embed(crop)

    active_run_id = run_id or new_run_id()
    run_dir = RUNS_DIR / active_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(run_dir / "probe.jpg"), img)
    cv2.imwrite(str(run_dir / "aligned_crop.png"), crop)

    state = {
        "run_id": active_run_id,
        "started_at": time.time(),
        "det_score": face.det_score,
        "is_live_capture": False,
        "public_image_url": public_image_url,
        "liveness": {"passed": True, "score": None, "label": "not_applicable"},
    }
    (run_dir / "run_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    table = Table(title=f"Scan Succeeded ({active_run_id})")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Run ID", active_run_id)
    table.add_row("BBox", f"{face.bbox[0]:.1f}, {face.bbox[1]:.1f}, {face.bbox[2]:.1f}, {face.bbox[3]:.1f}")
    table.add_row("Det Score", f"{face.det_score:.4f}")
    table.add_row("Aligned Crop", "112x112")
    table.add_row("Probe Vector", f"dim={len(embedding.vec)}, norm={np.linalg.norm(embedding.vec):.4f}")
    console.print(table)
    raise typer.Exit(EXIT_OK)


@app.command()
def search(
    run_id: str = typer.Argument(..., help="run id under runs/ to search for"),
    public_image_url: Optional[str] = typer.Option(None, "--public-image-url", help="Override public image URL"),
) -> None:
    """Runs the shared verification loop against web and social providers.

    Exit codes: 0 on MATCH, 3 on provider error, 4 on NO_MATCH.
    """
    run_dir = RUNS_DIR / run_id
    state_path = run_dir / "run_state.json"
    probe_img_path = run_dir / "probe.jpg"

    if not run_dir.exists() or not probe_img_path.exists():
        console.print(f"[bold red]Run directory or probe.jpg missing at {run_dir}[/]")
        raise typer.Exit(EXIT_VERIFICATION_MISMATCH)

    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    img = cv2.imread(str(probe_img_path))
    detector = FaceDetector()
    embedder = FaceEmbedder()

    faces = detector.detect(img)
    if not faces:
        console.print("[bold red]Could not re-detect face in probe.jpg[/]")
        raise typer.Exit(EXIT_NO_FACE)

    face = faces[0]
    crop = align(img, face.kps5)
    embedding = embedder.embed(crop)

    search_bytes = prepare_search_image(img)
    pub_url = public_image_url or state.get("public_image_url")

    web_detect = WebDetectProvider()
    bluesky = BlueskyProvider()

    primary_result = None
    if web_detect.available():
        primary_result = run_pipeline(
            search_bytes, embedding.vec, [web_detect], detector, embedder, public_image_url=pub_url
        )

    primary_produced_nothing = primary_result is None or not any(
        r.candidates_returned > 0 for r in primary_result.provider_reports
    )

    if primary_produced_nothing:
        if not bluesky.index:
            bluesky.crawl(detector, embedder)
        fallback_result = run_pipeline(
            search_bytes, embedding.vec, [bluesky], detector, embedder
        )
        combined_reports = (primary_result.provider_reports if primary_result else []) + fallback_result.provider_reports
        result = fallback_result
        providers_queried = [web_detect.name, bluesky.name] if primary_result else [bluesky.name]
        degraded = True
    else:
        result = primary_result
        combined_reports = primary_result.provider_reports
        providers_queried = [web_detect.name]
        degraded = False

    # Check for provider failure
    if not any(r.available and not r.error for r in combined_reports):
        console.print("[bold red]Provider error: all search providers failed or unavailable.[/]")
        raise typer.Exit(EXIT_PROVIDER_ERROR)

    audit = build_audit(
        run_id=run_id,
        started_at=state.get("started_at", time.time()),
        liveness=state.get("liveness", {"passed": True, "score": None, "label": "not_applicable"}),
        provider_reports=combined_reports,
        match=result.match,
        candidate_diagnostics=result.candidate_diagnostics,
    )
    audit["identity_signals"] = result.identity_signals
    audit["degraded_closed_corpus"] = degraded
    write_audit(run_dir, audit)

    if result.match.verdict == "MATCH":
        bundle = build_evidence(
            run_id=run_id,
            embedding=embedding,
            salt=get_commitment_salt(),
            liveness=LivenessResult(passed=True, score=0.0, label="not_applicable"),
            is_live_capture=state.get("is_live_capture", False),
            match=result.match,
            providers_queried=providers_queried,
            degraded_closed_corpus=degraded,
            identity_signals=result.identity_signals,
            candidates_examined=len(result.match.all_scored),
            pipeline_version=__version__,
            image_bytes=result.best_image_bytes,
        )
        (run_dir / "evidence.json").write_bytes(bundle.canonical_json)
        if result.best_image_bytes:
            ext = detect_image_extension(result.best_image_bytes)
            (run_dir / f"match_image{ext}").write_bytes(result.best_image_bytes)

        console.print(f"[bold green]MATCH found[/] for {run_id}")
        console.print(f"  URL:      {result.match.best.candidate.page_url}")
        console.print(f"  Score:    {result.match.best.score:.4f} (threshold: {result.match.threshold})")
        console.print(f"  Evidence: {bundle.evidence_hash_hex}")
        raise typer.Exit(EXIT_OK)

    console.print(f"[bold yellow]Verdict:[/] {result.match.verdict}")
    raise typer.Exit(EXIT_NO_MATCH)


@app.command(name="run-all")
def run_all(
    image_path: Path = typer.Argument(..., help="Path to input probe photograph"),
    public_image_url: Optional[str] = typer.Option(None, "--public-image-url", help="Public URL if image is hosted"),
    anchor: bool = typer.Option(True, "--anchor/--no-anchor", help="Anchor on EVM chain if MATCH"),
) -> None:
    """Executes scan -> search -> (optional) anchor end-to-end.

    Structured exit codes:
      0: OK / PASS
      1: Verification mismatch
      2: No face detected / quality gate failed
      3: Provider error
      4: NO_MATCH
      5: Chain / RPC error
    """
    if not image_path.exists():
        console.print(f"[bold red]File not found:[/] {image_path}")
        raise typer.Exit(EXIT_NO_FACE)

    img = cv2.imread(str(image_path))
    if img is None:
        console.print(f"[bold red]Could not decode image:[/] {image_path}")
        raise typer.Exit(EXIT_NO_FACE)

    detector = FaceDetector()
    faces = detector.detect(img)
    if not faces:
        console.print("[bold red]No face detected in input image.[/]")
        raise typer.Exit(EXIT_NO_FACE)

    face = faces[0]
    passes_q, q_reason = quality_passes(face)
    if not passes_q:
        console.print(f"[bold red]Face quality gate failed:[/] {q_reason}")
        raise typer.Exit(EXIT_NO_FACE)

    embedder = FaceEmbedder()
    crop = align(img, face.kps5)
    embedding = embedder.embed(crop)

    active_run_id = new_run_id()
    run_dir = RUNS_DIR / active_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(run_dir / "probe.jpg"), img)
    cv2.imwrite(str(run_dir / "aligned_crop.png"), crop)

    state = {
        "run_id": active_run_id,
        "started_at": time.time(),
        "det_score": face.det_score,
        "is_live_capture": False,
        "public_image_url": public_image_url,
        "liveness": {"passed": True, "score": None, "label": "not_applicable"},
    }
    (run_dir / "run_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    search_bytes = prepare_search_image(img)
    web_detect = WebDetectProvider()
    bluesky = BlueskyProvider()

    primary_result = None
    if web_detect.available():
        primary_result = run_pipeline(
            search_bytes, embedding.vec, [web_detect], detector, embedder, public_image_url=public_image_url
        )

    primary_produced_nothing = primary_result is None or not any(
        r.candidates_returned > 0 for r in primary_result.provider_reports
    )

    if primary_produced_nothing:
        if not bluesky.index:
            bluesky.crawl(detector, embedder)
        fallback_result = run_pipeline(
            search_bytes, embedding.vec, [bluesky], detector, embedder
        )
        combined_reports = (primary_result.provider_reports if primary_result else []) + fallback_result.provider_reports
        result = fallback_result
        providers_queried = [web_detect.name, bluesky.name] if primary_result else [bluesky.name]
        degraded = True
    else:
        result = primary_result
        combined_reports = primary_result.provider_reports
        providers_queried = [web_detect.name]
        degraded = False

    if not any(r.available and not r.error for r in combined_reports):
        console.print("[bold red]Provider error: all search providers failed.[/]")
        raise typer.Exit(EXIT_PROVIDER_ERROR)

    audit = build_audit(
        run_id=active_run_id,
        started_at=state["started_at"],
        liveness=state["liveness"],
        provider_reports=combined_reports,
        match=result.match,
        candidate_diagnostics=result.candidate_diagnostics,
    )
    audit["identity_signals"] = result.identity_signals
    audit["degraded_closed_corpus"] = degraded
    write_audit(run_dir, audit)

    if result.match.verdict != "MATCH":
        console.print(f"[bold yellow]Run completed with verdict:[/] {result.match.verdict}")
        raise typer.Exit(EXIT_NO_MATCH)

    bundle = build_evidence(
        run_id=active_run_id,
        embedding=embedding,
        salt=get_commitment_salt(),
        liveness=LivenessResult(passed=True, score=0.0, label="not_applicable"),
        is_live_capture=False,
        match=result.match,
        providers_queried=providers_queried,
        degraded_closed_corpus=degraded,
        identity_signals=result.identity_signals,
        candidates_examined=len(result.match.all_scored),
        pipeline_version=__version__,
        image_bytes=result.best_image_bytes,
    )
    (run_dir / "evidence.json").write_bytes(bundle.canonical_json)
    if result.best_image_bytes:
        ext = detect_image_extension(result.best_image_bytes)
        (run_dir / f"match_image{ext}").write_bytes(result.best_image_bytes)

    console.print(f"[bold green]MATCH found[/] ({active_run_id})")
    console.print(f"  URL:      {result.match.best.candidate.page_url}")
    console.print(f"  Score:    {result.match.best.score:.4f}")
    console.print(f"  Evidence: {bundle.evidence_hash_hex}")

    if anchor:
        try:
            client = EvmClient()
            console.print(f"anchoring on [bold]{client.chain_name}[/]...")
            receipt = client.anchor(bundle)
            anchor_record = {
                "tx_hash": receipt.tx_hash,
                "chain_id": receipt.chain_id,
                "block_number": receipt.block_number,
                "contract_address": receipt.contract_address,
                "gas_used": receipt.gas_used,
                "evidence_hash": receipt.evidence_hash_hex,
            }
            (run_dir / "anchor.json").write_text(json.dumps(anchor_record, indent=2), encoding="utf-8")
            console.print(f"[bold green]Anchored successfully:[/] tx={receipt.tx_hash}")
        except Exception as exc:
            console.print(f"[bold red]Chain error during anchoring:[/] {exc}")
            raise typer.Exit(EXIT_CHAIN_ERROR)

    raise typer.Exit(EXIT_OK)


@app.command()
def anchor(run_id: str = typer.Argument(..., help="run id under runs/, must contain evidence.json")) -> None:
    """Anchors an existing run's evidence bundle on the configured EVM chain (F8/F9)."""
    bundle_path = RUNS_DIR / run_id / "evidence.json"
    if not bundle_path.exists():
        console.print(f"[bold red]no evidence bundle at {bundle_path}[/]")
        raise typer.Exit(EXIT_VERIFICATION_MISMATCH)

    raw = bundle_path.read_bytes()
    data = json.loads(raw)
    bundle = EvidenceBundle(data=data, evidence_hash_hex=evidence_hash_hex(data), canonical_json=raw)

    try:
        client = EvmClient()
        console.print(f"anchoring on [bold]{client.chain_name}[/] (chain_id via RPC)...")
        receipt = client.anchor(bundle)
    except Exception as exc:
        console.print(f"[bold red]Chain error:[/] {exc}")
        raise typer.Exit(EXIT_CHAIN_ERROR)

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
    raise typer.Exit(EXIT_OK)


@app.command()
def verify(
    run_id: str = typer.Argument(..., help="run id under runs/, must contain evidence.json + anchor.json"),
    tamper: Optional[str] = typer.Option(
        None,
        "--tamper",
        help="Run tamper demonstration mode: swap-artifact | edit-bundle | forge-bundle",
    ),
) -> None:
    """Re-verifies a run's evidence bundle against on-chain anchor (T2.1 / T2.2 / F9).

    If --tamper is passed, demonstrates detection in an isolated copy and exits 0 on success.
    """
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        console.print(f"[bold red]Run directory not found:[/] {run_dir}")
        raise typer.Exit(EXIT_VERIFICATION_MISMATCH)

    client = EvmClient()

    if tamper:
        valid_modes = ("swap-artifact", "edit-bundle", "forge-bundle")
        if tamper not in valid_modes:
            console.print(f"[bold red]Invalid tamper mode:[/] {tamper}. Must be one of {valid_modes}")
            raise typer.Exit(EXIT_VERIFICATION_MISMATCH)

        console.print(f"Running tamper demonstration [bold cyan]{tamper}[/] on {run_id}...")
        try:
            tamper_res = tamper_run(run_dir, mode=tamper, client=client)
            expected_map = {
                "swap-artifact": "ARTIFACT_MISMATCH",
                "edit-bundle": "BUNDLE_MODIFIED",
                "forge-bundle": "NOT_ANCHORED",
            }
            expected = expected_map.get(tamper)
            console.print(f"  Result:  [bold yellow]{tamper_res.overall}[/]")
            console.print(f"  Target:  {tamper_res.tampered_target}")
            console.print(f"  Details: {tamper_res.detail}")

            if tamper_res.overall == expected:
                console.print(
                    f"[bold green]Tamper demonstration succeeded:[/] detected {tamper_res.overall} as expected."
                )
                raise typer.Exit(EXIT_OK)
            else:
                console.print(
                    f"[bold red]Tamper demonstration failed:[/] expected {expected}, got {tamper_res.overall}"
                )
                raise typer.Exit(EXIT_VERIFICATION_MISMATCH)
        except typer.Exit:
            raise
        except Exception as exc:
            console.print(f"[bold red]Error in tamper demo:[/] {exc}")
            raise typer.Exit(EXIT_VERIFICATION_MISMATCH)

    bundle_path = run_dir / "evidence.json"
    anchor_path = run_dir / "anchor.json"

    expected_hash = None
    if anchor_path.exists():
        expected_hash = json.loads(anchor_path.read_text(encoding="utf-8")).get("evidence_hash")

    report = reverify_bundle(bundle_path, client=client, expected_hash=expected_hash)
    console.print(format_report(report))

    if report.overall != "PASS":
        raise typer.Exit(EXIT_VERIFICATION_MISMATCH)
    raise typer.Exit(EXIT_OK)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the local demo UI (architecture.md 5a, prd.md G8/S13)."""
    import uvicorn

    console.print(f"[bold green]face-chain-verify demo UI[/] -> http://{host}:{port}")
    uvicorn.run("webapp.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()

