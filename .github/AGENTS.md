# GitHub automation

## Purpose
Run the TUI and publication guard checks for main pushes and pull requests.

## Local Contracts
Keep workflow token permissions at `contents: read`, disable checkout credential persistence, and pin actions to verified upstream commit hashes. Full-history checkout is required by the guard checks.

## Work Guidance
Preserve triggers, job names, Python versions and test commands when changing workflow security settings. Review upstream action pins before updating them.

Public CI does not replace the private prepublication checks for secrets, personal data and provenance. Keep private identity and provenance inputs out of Actions.

Dependency lower bounds allow CI resolution to vary between runs. A green run describes its resolved environment; it does not establish reproducibility through a dependency lock.

## Verification
Parse workflow YAML and review the exact diff. Confirm both `test` and `ref-gate` pass in GitHub Actions for the published commit; local syntax checks do not prove hosted execution.

The `python-floor` job uses Python 3.11 to ast-parse every tracked `tui/**/*.py` file (including files not imported during collection), failing on any SyntaxError, then installs TUI requirements and collects the suite. Confirm its hosted result for the published commit alongside `test` and `ref-gate`; local floor checks do not prove hosted execution.

## Child DOX Index
`workflows/tui-tests.yml` defines the checks.
