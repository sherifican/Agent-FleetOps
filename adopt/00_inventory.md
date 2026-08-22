# 00 — Inventory the host before configuration

Do not assume a GPU, model runner, service manager, shell, mount, or cloud CLI. This step writes the only input consumed by later setup documents: `adopt-scratch/inventory.md`. It observes; it does not install or start anything.

## Step 1 — create an inventory

**ADOPTER COMMAND** (from the repository root):

```bash
mkdir -p adopt-scratch
{
  printf 'captured_at_utc='; date -u +%Y-%m-%dT%H:%M:%SZ
  printf 'shell=%s\n' "${SHELL:-unknown}"
  printf '\n[service-managers]\n'
  for tool in systemctl launchctl rc-service; do command -v "$tool" || printf '%s=ABSENT\n' "$tool"; done
  printf '\n[runners]\n'
  for tool in ollama llama-server vllm; do command -v "$tool" || printf '%s=ABSENT\n' "$tool"; done
  printf '\n[gpu-nvidia]\n'
  if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi; else printf 'nvidia-smi=ABSENT\n'; fi
  printf '\n[gpu-amd]\n'
  if command -v rocm-smi >/dev/null 2>&1; then rocm-smi; else printf 'rocm-smi=ABSENT\n'; fi
  printf '\n[gpu-sysfs]\n'
  if [ -d /sys/class/drm ]; then find /sys/class/drm -maxdepth 1 -type l -printf '%f\n'; else printf 'sysfs-drm=ABSENT\n'; fi
} > adopt-scratch/inventory.md
sed -n '1,240p' adopt-scratch/inventory.md
```

**VERIFY — expected outcome:** output includes `captured_at_utc=`, `[runners]`, and each unavailable probe is recorded as `ABSENT`. A missing GPU or runner is an observation, not an error.

## Step 2 — classify the minimum viable path

Read the inventory and state the chosen path in `adopt-scratch/plan.md` before changing configuration:

- **Single box / no GPU:** one `local` TUI box; no device labels; no runner is required for the monitor to start.
- **GPU present, no runner:** one `local` box; record the GPU but leave model state to the monitor's safe-default readers. Do not install a runner without human direction.
- **Runner present:** use only its observed executable and any endpoint the human authorizes. The monitor itself makes no model calls.
- **Additional machine:** add a `remote` box only when state files are already mounted or relayed as local read-only paths. Do not infer an address or create a network tunnel.
- **No detected service manager or cloud CLI:** leave services and cloud integrations unconfigured. The package remains usable.

**ADOPTER COMMAND:**

```bash
test -s adopt-scratch/inventory.md && printf 'inventory-present\n'
```

**VERIFY — expected output:**

```text
inventory-present
```

## Step 3 — human gate for privileged changes

Before any later document proposes a cron entry, service, or shell hook, show the human `adopt-scratch/inventory.md`, the proposed plan, and the exact diff. Do not install, enable, or source anything until the human explicitly approves.

**VERIFY — expected outcome:** `MANUAL: obtain and record the human's approval; this cannot be verified by a shell command.`
