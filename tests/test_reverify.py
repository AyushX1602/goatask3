"""F9 exit criterion, run against the REAL live Anvil chain + deployed
contract — the actual scenario that will be filmed for the recording:

  1. anchor a real bundle
  2. save it to disk exactly as evidence.json would be
  3. reverify_bundle() -> PASS
  4. edit ONE character on disk
  5. reverify_bundle() -> TAMPERED, non-zero-equivalent result
"""

from __future__ import annotations

import json

import pytest
from web3 import Web3

from pipeline.chain.evm import EvmClient
from pipeline.chain.reverify import reverify_bundle
from pipeline.evidence.bundle import EvidenceBundle
from pipeline.evidence.canonical import canonical_bytes, evidence_hash_hex

ANVIL_RPC = "http://127.0.0.1:8545"


def _anvil_available() -> bool:
    try:
        return Web3(Web3.HTTPProvider(ANVIL_RPC, request_kwargs={"timeout": 2})).is_connected()
    except Exception:
        return False


requires_anvil = pytest.mark.skipif(
    not _anvil_available(), reason="Anvil not running on 127.0.0.1:8545"
)


def _make_bundle(tag: str) -> EvidenceBundle:
    data = {
        "schema_version": 1,
        "probe": {
            "face_commitment": "0x" + "11" * 32,
            "liveness_passed": True,
            "liveness_label": "not_applicable",
            "captured_at": 1757000000,
            "aligned_sha256": "22" * 32,
        },
        "match": {
            "page_url": "https://www.youtube.com/watch?v=nDj8MIyitUs",
            "image_url": "https://i.ytimg.com/vi/nDj8MIyitUs/oardefault.jpg",
            "image_sha256": "33" * 32,
            "provider": "gcv_web_detection",
            "score_bps": 9618,
            "margin_bps": 8475,
            "threshold_bps": 4200,
        },
        "post": {
            "platform": "youtube",
            "author_handle": "",
            "author_display": "bijay filmy",
            "text": f"reverify-test-{tag}",
            "published_at": 1756900000,
            "permalink": "https://www.youtube.com/watch?v=nDj8MIyitUs",
        },
        "run": {
            "run_id": f"reverify-{tag}",
            "providers_queried": ["web_detect"],
            "candidates_examined": 44,
            "candidates_rejected": 43,
            "degraded_closed_corpus": False,
            "identity_signals": ["Shah Rukh Khan"],
            "pipeline_version": "0.1.0",
            "model": "w600k_r50",
        },
    }
    canonical = canonical_bytes(data)
    return EvidenceBundle(data=data, evidence_hash_hex=evidence_hash_hex(data), canonical_json=canonical)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EVM_CHAIN", "anvil")
    monkeypatch.setenv("EVM_RPC_URL", ANVIL_RPC)
    monkeypatch.setenv("EVM_CONTRACT_ADDRESS", "0x5FbDB2315678afecb367f032d93F642f64180aa3")
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)
    return EvmClient()


@requires_anvil
def test_full_disk_round_trip_pass_then_tampered(client, tmp_path):
    """THE demo, exactly as it will be filmed."""
    bundle = _make_bundle(tag=str(id(client)))
    client.anchor(bundle)

    bundle_path = tmp_path / "evidence.json"
    bundle_path.write_bytes(bundle.canonical_json)
    # In real use this comes from the sibling anchor.json written at
    # anchor time (architecture.md 7) — recorded here explicitly since
    # this test IS the anchor step.
    anchored_hash = bundle.evidence_hash_hex

    report = reverify_bundle(bundle_path, client=client, expected_hash=anchored_hash)
    assert report.overall == "PASS", report.detail
    assert report.on_chain.exists is True
    assert report.on_chain.score_bps == 9618

    # THE tamper: flip one character on disk.
    tampered = bundle.canonical_json.replace(b"9618", b"9619")
    assert tampered != bundle.canonical_json
    bundle_path.write_bytes(tampered)

    report2 = reverify_bundle(bundle_path, client=client, expected_hash=anchored_hash)
    assert report2.overall == "TAMPERED", report2.detail
    assert report2.recomputed_hash != report.recomputed_hash


@requires_anvil
def test_never_anchored_bundle_reports_not_anchored(client, tmp_path):
    bundle = _make_bundle(tag=f"never-{id(client)}-unanchored")
    bundle_path = tmp_path / "evidence.json"
    bundle_path.write_bytes(bundle.canonical_json)

    report = reverify_bundle(bundle_path, client=client)
    assert report.overall == "NOT_ANCHORED"
    assert report.on_chain.exists is False


def test_missing_file_reports_error(client, tmp_path):
    report = reverify_bundle(tmp_path / "does_not_exist.json", client=client)
    assert report.overall == "ERROR"
    assert "not found" in report.detail


def test_invalid_json_reports_error(client, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    report = reverify_bundle(p, client=client)
    assert report.overall == "ERROR"
