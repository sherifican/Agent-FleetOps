# Adopt this repository with an AI agent

Give the agent this folder and say: **read `adopt/README.md` and follow it in order.** This package is written for an agent with shell access, not as a human tutorial. It starts with a host inventory, writes local configuration from those observations, presents planned diffs to the human before any cron entry, service, or shell hook is installed, then runs the available acceptance checks.

The package configures local files only. It adds no telemetry and makes no external calls except to model endpoints the adopter explicitly configures. A single box, a CPU-only host, and a host with no cloud CLI are supported paths: unavailable capabilities are recorded as `ABSENT` rather than substituted with guessed settings.

Run the numbered documents in order. Keep `adopt-scratch/inventory.md` local and untracked; later documents consume it.

| Order | Agent instruction |
| --- | --- |
| 1 | [00_inventory.md](00_inventory.md) — observe the host before choosing anything. |
| 2 | [10_tui.md](10_tui.md) — configure the file-only monitor. |
| 3 | [20_skills.md](20_skills.md) — install only the skills the agent system can actually load. |
| 4 | [30_guards.md](30_guards.md) — prove that the verification layer can fail. |
| 5 | [40_protocols.md](40_protocols.md) — propose, gate, and verify the operating disciplines. |
| 6 | [90_verify_all.md](90_verify_all.md) — fill the acceptance table and show it to the human. |

Every command below is an **ADOPTER COMMAND**: run it on the adopter's own machine, not on the export author's host. Do not treat an expected-output string as evidence; inspect the command's actual output.
