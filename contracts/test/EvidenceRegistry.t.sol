// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {EvidenceRegistry} from "../src/EvidenceRegistry.sol";

contract EvidenceRegistryTest is Test {
    EvidenceRegistry registry;

    bytes32 constant EVIDENCE_HASH = keccak256("evidence-1");
    bytes32 constant FACE_COMMITMENT = keccak256("commitment-1");
    bytes32 constant IMAGE_HASH = keccak256("image-1");
    bytes32 constant POST_HASH = keccak256("post-1");
    string constant CID = "bafy-example-cid";
    uint32 constant SCORE_BPS = 7685; // 0.7685, the real SRK/Obama-run scale

    function setUp() public {
        registry = new EvidenceRegistry();
    }

    /// The exit criterion in phases.md F8: anchor -> verify round trip.
    function test_AnchorThenVerify_RoundTrip() public {
        registry.anchor(EVIDENCE_HASH, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);

        (bool exists, EvidenceRegistry.Record memory rec) = registry.verify(EVIDENCE_HASH);

        assertTrue(exists);
        assertEq(rec.faceCommitment, FACE_COMMITMENT);
        assertEq(rec.imageHash, IMAGE_HASH);
        assertEq(rec.postHash, POST_HASH);
        assertEq(rec.cid, CID);
        assertEq(rec.scoreBps, SCORE_BPS);
        assertEq(rec.submitter, address(this));
        assertGt(rec.anchoredAt, 0);
    }

    /// The other half of the exit criterion: double-anchor reverts.
    function test_DoubleAnchor_Reverts() public {
        registry.anchor(EVIDENCE_HASH, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);

        vm.expectRevert(
            abi.encodeWithSelector(EvidenceRegistry.AlreadyAnchored.selector, EVIDENCE_HASH)
        );
        registry.anchor(EVIDENCE_HASH, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);
    }

    function test_ZeroHash_Reverts() public {
        vm.expectRevert(EvidenceRegistry.ZeroHash.selector);
        registry.anchor(bytes32(0), FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);
    }

    /// The tamper-detection property, at the contract layer: a hash that
    /// was never anchored (e.g. because the bundle was edited after
    /// anchoring and recomputed) must return exists=false, not revert and
    /// not return a stale/default record that looks real.
    function test_Verify_UnknownHash_ReturnsNotExists() public view {
        bytes32 neverAnchored = keccak256("this-was-never-anchored");
        (bool exists, EvidenceRegistry.Record memory rec) = registry.verify(neverAnchored);

        assertFalse(exists);
        assertEq(rec.anchoredAt, 0);
        assertEq(rec.faceCommitment, bytes32(0));
        assertEq(rec.submitter, address(0));
    }

    /// Simulates the exact tamper demo: anchor the real bundle's hash,
    /// then show that a ONE-CHARACTER-EDITED bundle's hash does not exist.
    function test_TamperDemo_EditedBundleHashDoesNotExist() public {
        bytes32 originalHash = keccak256(bytes('{"text":"hello world"}'));
        bytes32 tamperedHash = keccak256(bytes('{"text":"hello worlD"}'));
        assertTrue(originalHash != tamperedHash, "sanity: hashes must differ");

        registry.anchor(originalHash, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);

        (bool originalExists,) = registry.verify(originalHash);
        (bool tamperedExists,) = registry.verify(tamperedHash);

        assertTrue(originalExists, "the untouched bundle's hash must verify");
        assertFalse(tamperedExists, "the tampered bundle's hash must NOT verify");
    }

    function test_EmitsEvidenceAnchoredEvent() public {
        vm.expectEmit(true, true, false, true);
        emit EvidenceRegistry.EvidenceAnchored(EVIDENCE_HASH, FACE_COMMITMENT, CID, SCORE_BPS);
        registry.anchor(EVIDENCE_HASH, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);
    }

    function test_DifferentSubmitters_CanAnchorDifferentRecords() public {
        bytes32 hashA = keccak256("evidence-A");
        bytes32 hashB = keccak256("evidence-B");

        vm.prank(address(0x1));
        registry.anchor(hashA, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);

        vm.prank(address(0x2));
        registry.anchor(hashB, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, SCORE_BPS);

        (, EvidenceRegistry.Record memory recA) = registry.verify(hashA);
        (, EvidenceRegistry.Record memory recB) = registry.verify(hashB);
        assertEq(recA.submitter, address(0x1));
        assertEq(recB.submitter, address(0x2));
    }

    /// Fuzz: no valid (non-zero) evidenceHash + scoreBps combination should
    /// ever fail to round-trip through anchor -> verify.
    function testFuzz_AnchorVerify_RoundTrip(bytes32 hash, uint32 scoreBps) public {
        vm.assume(hash != bytes32(0));
        registry.anchor(hash, FACE_COMMITMENT, IMAGE_HASH, POST_HASH, CID, scoreBps);

        (bool exists, EvidenceRegistry.Record memory rec) = registry.verify(hash);
        assertTrue(exists);
        assertEq(rec.scoreBps, scoreBps);
    }
}
