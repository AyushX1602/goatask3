// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title EvidenceRegistry
/// @notice Anchors a tamper-evident commitment to a face-to-social-post
///         match, and allows anyone to re-verify a bundle against the
///         on-chain record later.
/// @dev R-01 (see the Python pipeline's rules.md): this contract stores NO
///      biometric data and NO raw face embedding. `faceCommitment` is
///      keccak256(salt || quantised_embedding), computed off-chain in
///      pipeline/evidence/commitment.py. The salt never leaves the
///      operator's machine. An immutable public ledger plus a recoverable
///      biometric template would be a permanent, un-deletable biometric
///      record — a direct collision with GDPR Art. 9 and India's DPDP Act.
///      The commitment still lets a holder of the salt prove later that a
///      specific face produced this record, without ever publishing
///      anything biometric.
contract EvidenceRegistry {
    /// @dev All fields mirror pipeline/evidence/bundle.py's canonical
    ///      structure exactly, so `evidenceHash` (the mapping key) is
    ///      reproducible by re-running pipeline.evidence.canonical.
    ///      evidence_hash_hex over the same JSON bundle.
    struct Record {
        bytes32 faceCommitment; // keccak256(salt || quantised embedding) — never the raw vector
        bytes32 imageHash;      // sha256 of the matched candidate image bytes
        bytes32 postHash;       // sha256 of the canonical post metadata
        string  cid;            // IPFS CID of the full evidence bundle, or "" if not uploaded
        uint32  scoreBps;       // cosine similarity, basis points (0-10000)
        uint64  anchoredAt;     // block.timestamp at anchor time
        address submitter;      // who anchored this record
    }

    mapping(bytes32 => Record) private _records; // key = evidenceHash

    event EvidenceAnchored(
        bytes32 indexed evidenceHash,
        bytes32 indexed faceCommitment,
        string  cid,
        uint32  scoreBps
    );

    error AlreadyAnchored(bytes32 evidenceHash);
    error ZeroHash();

    /// @notice Anchors a new evidence record. Reverts if this exact
    ///         evidenceHash has already been anchored (R-16 in the
    ///         Python pipeline: a record is anchored once; re-running the
    ///         search on the same input should not silently overwrite it).
    function anchor(
        bytes32 evidenceHash,
        bytes32 faceCommitment,
        bytes32 imageHash,
        bytes32 postHash,
        string calldata cid,
        uint32  scoreBps
    ) external {
        if (evidenceHash == bytes32(0)) revert ZeroHash();
        if (_records[evidenceHash].anchoredAt != 0) revert AlreadyAnchored(evidenceHash);

        _records[evidenceHash] = Record({
            faceCommitment: faceCommitment,
            imageHash:      imageHash,
            postHash:       postHash,
            cid:            cid,
            scoreBps:       scoreBps,
            anchoredAt:     uint64(block.timestamp),
            submitter:      msg.sender
        });

        emit EvidenceAnchored(evidenceHash, faceCommitment, cid, scoreBps);
    }

    /// @notice Re-verifies a bundle: given the SAME evidenceHash a caller
    ///         recomputes from a (possibly tampered) local bundle, checks
    ///         whether that exact hash was ever anchored.
    /// @dev This IS the "demonstrate re-verifying the data against the
    ///      on-chain record" requirement. If a bundle is edited after
    ///      anchoring, its recomputed hash will not match any key in
    ///      `_records`, and `exists` returns false — the tamper-detection
    ///      property the whole design exists to prove.
    function verify(bytes32 evidenceHash)
        external
        view
        returns (bool exists, Record memory rec)
    {
        rec = _records[evidenceHash];
        exists = rec.anchoredAt != 0;
    }
}
