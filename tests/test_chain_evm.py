"""F8/F9 exit criteria (docs/phases.md FINAL PLAN):

'anchor -> verify round trip green; double-anchor reverts'
'verify PASSes on an untouched bundle; a one-character edit reports
TAMPERED with a non-zero exit code'

These run against a REAL Anvil chain — not mocked — because the whole
point of this layer is proving the on-chain re-verification actually
works. Requires `anvil` running on 127.0.0.1:8545 and the contract
deployed (see contracts/README or docs/phases.md F8).

Skipped automatically if Anvil is not reachable, so the rest of the suite
is unaffected by whether a chain happens to be running.
"""

from __future__ import annotations

import json

import pytest
from web3 import Web3

from pipeline.chain.evm import ContractNotDeployedError, EvmClient
from pipeline.evidence.bundle import EvidenceBundle
from pipeline.evidence.canonical import canonical_bytes, evidence_hash_hex

ANVIL_RPC = "http://127.0.0.1:8545"


def _anvil_available() -> bool:
    try:
        w3 = Web3(Web3.HTTPProvider(ANVIL_RPC, request_kwargs={"timeout": 2}))
        return w3.is_connected()
    except Exception:
        return False


requires_anvil = pytest.mark.skipif(
    not _anvil_available(), reason="Anvil not running on 127.0.0.1:8545"
)


def _make_bundle(text: str = "test evidence bundle", score_bps: int = 7685) -> EvidenceBundle:
    data = {
        "schema_version": 2,
        "probe": {
            "face_commitment": "0x" + "ab" * 32,
            "liveness_passed": True,
            "liveness_label": "not_applicable",
            "captured_at": 1757000000,
            "aligned_sha256": "cd" * 32,
        },
        "match": {
            "page_url": "https://www.youtube.com/watch?v=nDj8MIyitUs",
            "image_url": "https://i.ytimg.com/vi/nDj8MIyitUs/maxresdefault.jpg",
            "image_sha256": "ef" * 32,
            "image_phash": "aabbccdd11223344",
            "verified_against": "search_engine_cache",
            "provider": "gcv_web_detection",
            "score_bps": score_bps,
            "margin_bps": 8475,
            "threshold_bps": 4200,
        },
        "post": {
            "platform": "youtube",
            "content_kind": "post",
            "author_handle": "",
            "author_display": "bijay filmy",
            "text": text,
            "published_at": 1756900000,
            "permalink": "https://www.youtube.com/watch?v=nDj8MIyitUs",
        },
        "run": {
            "run_id": "test-run",
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
def test_connects_to_the_real_anvil_chain(client):
    assert client.w3.is_connected()
    assert client.w3.eth.chain_id == 31337


@requires_anvil
def test_anchor_then_verify_round_trip(client):
    """The core F8/F9 exit criterion."""
    bundle = _make_bundle(text=f"round-trip-{id(client)}")
    receipt = client.anchor(bundle)

    assert receipt.chain_id == 31337
    assert receipt.tx_hash.startswith("0x")
    assert receipt.contract_address

    result = client.verify(bundle.evidence_hash_hex)
    assert result.exists is True
    assert result.score_bps == 7685
    assert result.face_commitment_hex == "0x" + "ab" * 32


@requires_anvil
def test_double_anchor_reverts(client):
    bundle = _make_bundle(text=f"double-anchor-{id(client)}-unique")
    client.anchor(bundle)

    with pytest.raises(Exception):  # web3 raises ContractLogicError on revert
        client.anchor(bundle)


@requires_anvil
def test_unanchored_hash_returns_not_exists(client):
    never_anchored = "0x" + "99" * 32
    result = client.verify(never_anchored)
    assert result.exists is False
    assert result.score_bps is None


@requires_anvil
def test_tamper_demo_end_to_end(client):
    """THE demo: anchor a real bundle, verify it passes, edit ONE
    character, recompute the hash, verify it fails. This is the literal
    text of brief requirement 3."""
    bundle = _make_bundle(text=f"tamper-demo-{id(client)}")
    client.anchor(bundle)

    # Step 1: untouched bundle verifies.
    original_result = client.verify(bundle.evidence_hash_hex)
    assert original_result.exists is True

    # Step 2: simulate re-loading the bundle from disk and tampering with it.
    tampered_bytes = bundle.canonical_json.replace(b"tamper-demo", b"tamper-demO")
    assert tampered_bytes != bundle.canonical_json, "sanity: the edit must actually change the bytes"

    tampered_data = json.loads(tampered_bytes)
    tampered_hash = evidence_hash_hex(tampered_data)
    assert tampered_hash != bundle.evidence_hash_hex, "sanity: hash must change"

    # Step 3: re-verify the TAMPERED hash against the chain.
    tampered_result = client.verify(tampered_hash)
    assert tampered_result.exists is False, "a tampered bundle must NOT verify"


@requires_anvil
def test_anvil_default_key_used_automatically_when_no_env_key_set(monkeypatch):
    """D-27: the required local chain must work with zero setup."""
    monkeypatch.setenv("EVM_CHAIN", "anvil")
    monkeypatch.setenv("EVM_RPC_URL", ANVIL_RPC)
    monkeypatch.setenv("EVM_CONTRACT_ADDRESS", "0x5FbDB2315678afecb367f032d93F642f64180aa3")
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)

    client = EvmClient()
    assert client.account.address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_non_anvil_chain_requires_an_explicit_key(monkeypatch):
    """Never silently fall back to the public Anvil key on a real chain —
    that would be a real security bug, not a convenience."""
    monkeypatch.setenv("EVM_CHAIN", "base-sepolia")
    monkeypatch.delenv("EVM_PRIVATE_KEY", raising=False)

    with pytest.raises(ValueError, match="EVM_PRIVATE_KEY"):
        EvmClient()


def test_missing_contract_address_raises_a_clear_error(monkeypatch):
    monkeypatch.setenv("EVM_CHAIN", "anvil")
    monkeypatch.delenv("EVM_CONTRACT_ADDRESS", raising=False)

    client = EvmClient(rpc_url=ANVIL_RPC)
    with pytest.raises(ContractNotDeployedError):
        client.verify("0x" + "00" * 32)


def test_base_sepolia_rpc_switch(monkeypatch):
    """R-15: EVM_CHAIN=base-sepolia automatically switches RPC endpoint to https://sepolia.base.org."""
    monkeypatch.setenv("EVM_CHAIN", "base-sepolia")
    monkeypatch.setenv("EVM_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("EVM_CONTRACT_ADDRESS", "0x5FbDB2315678afecb367f032d93F642f64180aa3")
    monkeypatch.delenv("EVM_RPC_URL", raising=False)

    client = EvmClient()
    assert client.chain_name == "base-sepolia"
    assert client.rpc_url == "https://sepolia.base.org"

