# GitHub automation

## Purpose
Run the TUI and publication guard checks for main pushes and pull requests.

## Local Contracts
Keep workflow token permissions at `contents: read`, disable checkout credential persistence, and pin actions to verified upstream commit hashes. Full-history checkout is required by the guard checks.

## Work Guidance
Preserve triggers, job names, Python versions and test commands when changing workflow security settings. Review upstream action pins before updating them.

Public CI does not replace the private prepublication checks for secrets, personal data and provenance. Keep private identity and provenance inputs out of Actions.

Dependency lower bounds allow CI resolution to vary between runs. A green run describes its resolved environment; it does not establish reproducibility through a dependency lock.

After the `test` job on `3857c81` reached GitHub's six-hour cancellation limit while its guard step was running (20,682 s), the job is bounded at 100 minutes and its hermetic and guard steps at 45 minutes each. The same guard step on `ee18a1d` took 584 s on the hosted runner and 423 s on a local machine (1.38×). On that local machine the bounded suite's whole guard step took 676 s, of which its `pytest guard/tests/` took 670 s, so the 45-minute guard deadline leaves about three times the expected hosted time. The hermetic step took 970 s on `3857c81`, so its 45-minute deadline leaves about 2.8 times that. The `python-floor` and `ref-gate` jobs are bounded at 10 minutes each. Preserve these deadlines when editing the workflow.

## Verification
Parse workflow YAML and review the exact diff. Confirm both `test` and `ref-gate` pass in GitHub Actions for the published commit; local syntax checks do not prove hosted execution.

The `python-floor` job uses Python 3.11 to ast-parse every tracked `tui/**/*.py` file (including files not imported during collection), failing on any SyntaxError, then installs TUI requirements and collects the suite. Confirm its hosted result for the published commit alongside `test` and `ref-gate`; local floor checks do not prove hosted execution.

## Child DOX Index
`workflows/tui-tests.yml` defines the checks.
