"""Canonical JSON serialisation. See docs/design.md 4.1, R-02.

This is where hash-anchoring demos die: if serialisation drifts between
producing a bundle and re-verifying it later, the hash changes and
verification fails on stage. The rules here are frozen and must not be
relaxed once real evidence bundles start getting committed.

Rules (R-02):
  - UTF-8, lexicographically sorted keys, no insignificant whitespace
  - NO FLOATS anywhere in a hashed structure. Similarity -> integer basis
    points. Timestamps -> integer Unix seconds. Anything else numeric that
    might carry a fraction must be converted before it reaches this module.
  - schema_version is mandatory on every top-level bundle.
"""

from __future__ import annotations

import hashlib
import json

from eth_hash.auto import keccak


class NonCanonicalValueError(ValueError):
    """Raised when a structure contains a value that would make hashing
    non-reproducible — a float, or a NaN/Infinity, most likely."""


def _reject_floats(obj, path: str = "$") -> None:
    """Walks the structure and raises if any float is found. bool is a
    subclass of int in Python, so it is explicitly excluded from the float
    check (isinstance(True, float) is False anyway, but this keeps the
    walk explicit and testable).
    """
    if isinstance(obj, float):
        raise NonCanonicalValueError(
            f"float found at {path}: {obj!r} — convert to an integer "
            "(e.g. basis points, Unix seconds) before canonicalising (R-02)"
        )
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise NonCanonicalValueError(f"non-string key at {path}: {k!r}")
            _reject_floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_floats(v, f"{path}[{i}]")


def canonical_bytes(obj: dict) -> bytes:
    """RFC 8785-equivalent for our purposes: sorted keys, no whitespace,
    UTF-8, and (R-02) no floats anywhere in the structure.

    Raises NonCanonicalValueError rather than silently coercing a float,
    because silent coercion is exactly the kind of drift that breaks
    verification later without anyone noticing at hash time.
    """
    _reject_floats(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def keccak256(data: bytes) -> bytes:
    """Ethereum-compatible keccak256 (NOT the NIST SHA3-256 variant — they
    differ in padding). Used for the evidence hash and the face commitment,
    both of which need to be verifiable against an EVM contract later."""
    return keccak(data)


def evidence_hash(obj: dict) -> bytes:
    """keccak256 of the canonical bytes. This is the value a future
    contract's anchor()/verify() functions would operate on."""
    return keccak256(canonical_bytes(obj))


def evidence_hash_hex(obj: dict) -> str:
    return "0x" + evidence_hash(obj).hex()


def round_trip_bytes(data: bytes) -> bytes:
    """canonical_bytes(json.loads(data)) — used by the CI round-trip test
    (docs/design.md 4.1): canonicalising, parsing, and re-canonicalising must be
    byte-identical, or something in the pipeline is producing values that
    are not stable under serialisation."""
    return canonical_bytes(json.loads(data))
