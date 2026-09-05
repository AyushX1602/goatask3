# EvidenceRegistry — F8/F9

Solidity contract + Foundry project. Anchors a tamper-evident commitment
to a face-to-social-post match, and lets anyone re-verify a bundle against
the on-chain record later. See `../rules.md` R-01, R-15, and
`../phases.md` F8/F9 for the design rationale.

## Setup (fresh clone)

`lib/forge-std` is gitignored (it's a git submodule-style dependency, not
our code). Restore it once:

```powershell
forge install foundry-rs/forge-std --no-commit
```

## Build and test

```powershell
forge build
forge test -vv
```

8 tests, including a 256-run fuzz test and an explicit tamper-detection
test (`test_TamperDemo_EditedBundleHashDoesNotExist`).

## Deploy to Anvil (required chain, D-27)

Start Anvil in one terminal:

```powershell
anvil
```

Deploy in another (uses Anvil's well-known default account #0 — public,
documented, only ever funded on this ephemeral local chain):

```powershell
$env:PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
forge script script/Deploy.s.sol:Deploy --rpc-url http://127.0.0.1:8545 --broadcast
```

Copy the printed contract address into `.env` as `EVM_CONTRACT_ADDRESS`.
**Anvil resets on every restart** — you must redeploy and update `.env`
each time you start a fresh Anvil process.

## Deploy to Base Sepolia (optional bonus, not required)

Same script, different RPC and a real funded key — R-15: identical code
path, only the flags differ.

```powershell
forge script script/Deploy.s.sol:Deploy --rpc-url https://sepolia.base.org `
  --private-key $env:EVM_PRIVATE_KEY --broadcast --verify
```

## Using it from Python

`pipeline/chain/evm.py` reads `EVM_CHAIN`, `EVM_RPC_URL`,
`EVM_CONTRACT_ADDRESS` from `.env` and talks to whichever chain is
configured through the exact same code path (R-15). See:

```powershell
python -m pipeline anchor <run_id>
python -m pipeline verify <run_id>
```
