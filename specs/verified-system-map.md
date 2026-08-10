# The Verified System Map — documentation with drift tripwires

System documentation rots because nothing fails when it becomes wrong. This pattern makes the map
**verifiable**: every section carries machine-checkable drift rules, a linter re-verifies them, and
a section that can't be verified says so on its face. Run in production on a multi-machine agent
fleet; the mechanism is fully general.

## Structure

The map is a directory of numbered sections, each owning one subsystem:

```
map/
  00-hardware.md          # boxes, GPUs, capacity
  01-network-bridges.md   # links between machines, transports
  02-services.md          # daemons, ports, schedules
  ...
  manifest.json           # per-section drift rules + verification stamps
  lint.py                 # re-verifies every rule, re-stamps or flags
```

Each section states **verified facts** — things checked against the live system when written, with
the check date. The manifest pairs each section with **drift rules**: cheap, mechanical probes that
would notice if the documented claim stopped being true.

```json
{
  "02-services.md": {
    "verified": "2026-08-01",
    "drift_rules": [
      {"probe": "systemctl --user is-active gateway.service", "expect": "active",
       "claims": "the gateway runs as a user service"},
      {"probe": "ss -ltn | grep -c ':8642'", "expect": "1",
       "claims": "the API listens on loopback :8642"}
    ]
  }
}
```

## The rules

1. **Format verified facts, never free-extract.** A section is written from command output the
   author actually ran, not from memory of the system. The map is a *record of observations*, not a
   description of intentions. (An agent writing "the service auto-restarts" because that's typical
   is manufacturing documentation; the pattern exists to prevent exactly that.)

2. **Every claim worth documenting gets a probe that could refute it.** If no cheap probe can tell
   the claim went stale, either the claim is too vague to document or it needs rewording until it is
   probeable. This is falsifiability applied to documentation.

3. **The linter re-stamps or flags — never silently passes.** `lint.py` runs every drift rule; a
   section whose probes all pass gets a fresh verification stamp, a section with a failing probe is
   flagged `DRIFTED` with the failing rule named. The stamp date is in the section header, so a
   reader always sees how stale the verification is.

4. **Change the system → change the map in the same motion.** Any work that adds/removes a
   service, bridge, schedule, or machine proposes the matching section update *and* the matching
   drift-rule update, then runs the linter. A map edit without a rule edit is suspect: the claim
   changed but its tripwire didn't.

5. **Human-gated content, machine-gated freshness.** What the map *says* is reviewed by a person;
   whether it's still *true* is checked by machine. Neither substitutes for the other.

6. **A section that can't be probed says so.** Some facts (a purchase, a decision, an external
   account) have no live probe. They're marked `UNVERIFIABLE-BY-PROBE` with the evidence that
   established them — distinct from "verified," never dressed as it.

## Why this beats a wiki

A wiki answers "what did someone once believe?" This answers "what was true on the stamp date, and
would we notice if it changed?" For agent fleets the difference is operational: agents plan against
the map, and an agent planning against stale topology produces confidently wrong work. The linter
turns that from a silent hazard into a red flag on the exact section that drifted.

## Minimal adoption

Start with three sections and five drift rules. The discipline — probe-or-reword, stamp dates,
same-motion updates — matters more than coverage. A map with ten verified claims beats a map with
two hundred believed ones.
