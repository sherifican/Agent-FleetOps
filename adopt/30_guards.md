# 30 — Adopt the guard suite

The guard suite is useful only when its failure path has been observed. `teeth_prover` mutates a known fixture so every covered guard must go red. Guard exit codes are contractual: `0` means clean, `1` means violation, and `2` means `UNMEASURED`; `2` dominates `1`.

## Step 1 — run the tooth check before trusting another result

**ADOPTER COMMAND:**

```bash
python3 guard/teeth_prover.py
```

**VERIFY — expected output:** a `SUMMARY:` line with no `UNPROVABLE` entries and process exit `0`. If a mutation is `VACUOUS`, `NOT_APPLIED`, `BAD_FIXTURE`, or `UNMEASURED`, stop: the suite has not established that its checks can fail.

## Step 2 — check vocabulary agreement and hermetic guard tests

**ADOPTER COMMAND:**

```bash
python3 guard/contract_agreement.py
python3 -m pytest guard/tests/ -q
```

**VERIFY — expected output:** both commands exit `0`. The first prints agreement rows; the second reports the guard test count. Keep the literal output with the adoption record.

## Step 3 — understand the aggregate runner

**ADOPTER COMMAND:**

```bash
guard/run_guards.sh
printf 'runner-exit=%s\n' "$?"
```

**VERIFY — expected output:** the default runner ends with `UNMEASURED` and `runner-exit=2`, because its liveness stage is deliberately dry-run. This is not a clean acceptance result.

`guard/run_guards.sh --with-canary` may make liveness calls to endpoints configured by the adopter. Treat that as an external operation: show its target list and obtain human approval first. Do not run it merely to make the aggregate result appear clean.

## Step 4 — do not silently install the commit hook

The repository contains `guard/hooks/install.sh`. It can change the adopter's Git hook configuration. Present the script, its target path, and its proposed diff to the human before any invocation.

**ADOPTER COMMAND:**

```bash
sed -n '1,240p' guard/hooks/install.sh
```

**VERIFY — expected outcome:** `MANUAL: the human approves or rejects hook installation after reading the script. No hook is installed by this package.`
