"""Tests for CLI subcommands and structured exit codes (T2.7 / docs/phases.md).

Exit Code Schema:
  0: OK / PASS
  1: Verification mismatch / tampered / artifact failure
  2: No face detected or quality gate failure
  3: Provider error / network failure
  4: Search completed with NO_MATCH
  5: Chain / RPC error
"""

from __future__ import annotations

import json
from pathlib import Path
import cv2
import numpy as np
from typer.testing import CliRunner

from pipeline.cli import (
    EXIT_CHAIN_ERROR,
    EXIT_NO_FACE,
    EXIT_NO_MATCH,
    EXIT_OK,
    EXIT_PROVIDER_ERROR,
    EXIT_VERIFICATION_MISMATCH,
    app,
)
from pipeline.search.base import ProviderReport
from pipeline.verify.matcher import MatchResult
from pipeline.verify.pipeline_run import PipelineResult

runner = CliRunner()
FIX = Path(__file__).parent / "fixtures"


def test_cli_scan_exit_code_0_on_face(tmp_path, monkeypatch):
    """Exit code 0: scan successfully detects and embeds face."""
    monkeypatch.setattr("pipeline.cli.RUNS_DIR", tmp_path)
    res = runner.invoke(app, ["scan", str(FIX / "obama1.jpg"), "--run-id", "test-scan-0"])
    assert res.exit_code == EXIT_OK
    assert (tmp_path / "test-scan-0" / "probe.jpg").exists()
    assert (tmp_path / "test-scan-0" / "run_state.json").exists()


def test_cli_scan_exit_code_2_on_no_face(tmp_path, monkeypatch):
    """Exit code 2: no face found in input image."""
    blank_img_path = tmp_path / "blank.jpg"
    cv2.imwrite(str(blank_img_path), np.zeros((200, 200, 3), dtype=np.uint8))

    monkeypatch.setattr("pipeline.cli.RUNS_DIR", tmp_path)
    res = runner.invoke(app, ["scan", str(blank_img_path)])
    assert res.exit_code == EXIT_NO_FACE
    assert "No face detected" in res.output or "face" in res.output.lower()


def test_cli_verify_exit_code_0_on_anchored_pass():
    """Exit code 0: re-verifying a valid anchored run passes."""
    res = runner.invoke(app, ["verify", "2026-09-05T18-07-40Z"])
    assert res.exit_code == EXIT_OK
    assert "PASS" in res.output


def test_cli_verify_exit_code_1_on_tampered_bundle(tmp_path, monkeypatch):
    """Exit code 1: re-verifying a modified bundle reports failure."""
    import shutil
    from pipeline.config import RUNS_DIR

    src_dir = RUNS_DIR / "2026-09-05T18-07-40Z"
    dst_dir = tmp_path / "tampered-run"
    shutil.copytree(src_dir, dst_dir)

    # Tamper evidence.json
    ev_path = dst_dir / "evidence.json"
    ev_data = json.loads(ev_path.read_text(encoding="utf-8"))
    ev_data["pipeline_version"] = "99.99.99"
    ev_path.write_text(json.dumps(ev_data, indent=2), encoding="utf-8")

    monkeypatch.setattr("pipeline.cli.RUNS_DIR", tmp_path)
    res = runner.invoke(app, ["verify", "tampered-run"])
    assert res.exit_code == EXIT_VERIFICATION_MISMATCH
    assert "BUNDLE_MODIFIED" in res.output or "MISMATCH" in res.output


def test_cli_verify_tamper_demo_modes():
    """Exit code 0: --tamper mode demonstration succeeds for all 3 modes."""
    for mode in ("swap-artifact", "edit-bundle", "forge-bundle"):
        res = runner.invoke(app, ["verify", "2026-09-05T18-07-40Z", "--tamper", mode])
        assert res.exit_code == EXIT_OK
        assert "Tamper demonstration succeeded" in res.output


def test_cli_search_exit_code_4_on_no_match(tmp_path, monkeypatch):
    """Exit code 4: search completes with NO_MATCH."""
    monkeypatch.setattr("pipeline.cli.RUNS_DIR", tmp_path)
    # First scan
    run_id = "test-run-no-match"
    scan_res = runner.invoke(app, ["scan", str(FIX / "obama1.jpg"), "--run-id", run_id])
    assert scan_res.exit_code == EXIT_OK

    # Mock run_pipeline to return NO_MATCH
    fake_match = MatchResult(
        best=None,
        runner_up=None,
        all_scored=[],
        threshold=0.42,
        margin_required=0.08,
        verdict="NO_MATCH",
    )
    fake_pipeline_res = PipelineResult(
        match=fake_match,
        provider_reports=[ProviderReport(name="mock", available=True, attempted=True, candidates_returned=1)],
        identity_signals=[],
        images_fetched=0,
        images_deduped=0,
    )
    monkeypatch.setattr("pipeline.cli.run_pipeline", lambda *a, **kw: fake_pipeline_res)

    search_res = runner.invoke(app, ["search", run_id])
    assert search_res.exit_code == EXIT_NO_MATCH
    assert "NO_MATCH" in search_res.output


def test_cli_search_exit_code_3_on_provider_error(tmp_path, monkeypatch):
    """Exit code 3: provider network error or failure."""
    monkeypatch.setattr("pipeline.cli.RUNS_DIR", tmp_path)
    run_id = "test-run-provider-err"
    runner.invoke(app, ["scan", str(FIX / "obama1.jpg"), "--run-id", run_id])

    fake_match = MatchResult(
        best=None,
        runner_up=None,
        all_scored=[],
        threshold=0.42,
        margin_required=0.08,
        verdict="NO_CANDIDATES",
    )
    fake_pipeline_res = PipelineResult(
        match=fake_match,
        provider_reports=[
            ProviderReport(name="gcv", available=True, attempted=True, error="APIQuotaExceeded"),
            ProviderReport(name="bluesky", available=True, attempted=True, error="ConnectionRefused"),
        ],
        identity_signals=[],
        images_fetched=0,
        images_deduped=0,
    )
    monkeypatch.setattr("pipeline.cli.run_pipeline", lambda *a, **kw: fake_pipeline_res)

    search_res = runner.invoke(app, ["search", run_id])
    assert search_res.exit_code == EXIT_PROVIDER_ERROR
    assert "Provider error" in search_res.output


def test_cli_anchor_exit_code_5_on_chain_error(monkeypatch):
    """Exit code 5: EVM RPC or contract anchor error."""
    class BrokenEvmClient:
        chain_name = "test-chain"
        def anchor(self, bundle):
            raise ConnectionError("RPC timeout connecting to http://127.0.0.1:8545")

    monkeypatch.setattr("pipeline.cli.EvmClient", BrokenEvmClient)
    res = runner.invoke(app, ["anchor", "2026-09-05T18-07-40Z"])
    assert res.exit_code == EXIT_CHAIN_ERROR
    assert "Chain error" in res.output
