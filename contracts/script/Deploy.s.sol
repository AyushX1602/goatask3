// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {EvidenceRegistry} from "../src/EvidenceRegistry.sol";

/// @dev Deploys EvidenceRegistry. Same script for Anvil (required, D-27)
///      and Base Sepolia (optional bonus) — only the --rpc-url and
///      --private-key flags differ, never the code path (R-15).
///
///      Anvil:
///        forge script script/Deploy.s.sol:Deploy --rpc-url http://127.0.0.1:8545 \
///          --private-key <anvil-default-key> --broadcast
///
///      Base Sepolia:
///        forge script script/Deploy.s.sol:Deploy --rpc-url $BASE_SEPOLIA_RPC_URL \
///          --private-key $EVM_PRIVATE_KEY --broadcast --verify
contract Deploy is Script {
    function run() external returns (EvidenceRegistry) {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        vm.startBroadcast(deployerKey);

        EvidenceRegistry registry = new EvidenceRegistry();
        console.log("EvidenceRegistry deployed at:", address(registry));

        vm.stopBroadcast();
        return registry;
    }
}
