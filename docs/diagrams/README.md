# Operations diagrams

These figures explain selected procedures. Apply only the selected workflow's rules.
They grant no permission to execute, install, publish, or activate anything.

- [Roles and channels](#roles-and-channels)
- [Memory stores](#memory-stores)
- [Review and handoff](#review-and-handoff)
- [Optional recommendation: inert package handoff](#optional-recommendation-inert-package-handoff)

Render the tall canvases with a window padded by 200 source pixels, then crop to the stated output size.
The commands below are a host handoff procedure, not a render receipt for these revised sources.

**Preview status:** PNG rendering is pending. The image references below designate the intended previews;
use the SVG sources and text equivalents until those previews have been rendered and inspected.

At a 700 px display width, body type is 14 px and title type is 21 px.
At smaller widths, use the adjacent text equivalent or open the full-size asset.
D with a square denotes Documented; R with a circle denotes Recommendation.
In Roles and channels and Review and handoff, solid arrows show information or artifact flow.
In Memory stores, solid arrows show labelled relationships; each label names the relationship.
The dot denotes a source set by the selected configuration. Dotted arrows show authorization decisions.
Dashed frames indicate optional examples. Evidence class and optionality are separate.

## Roles and channels

R Apply only the selected workflow's rules.

<img src="roles-and-channels.png" width="700" alt="Roles, request and reply permissions, separate authorization, and a destination check. A missing required reviewer is a gap that stops the selected protocol. An optional link leads to the inert-handoff recipe." />

[Full-size PNG](roles-and-channels.png) · [SVG source](roles-and-channels.svg)

**Alt text:** Roles, request and reply permissions, separate authorization, and a destination check. A missing required reviewer is a gap that stops the selected protocol. An optional link leads to the inert-handoff recipe.

**Dimensions:** SVG 1200 × 1560; intended PNG 2400 × 3120. PNG dimensions and appearance await rendering.

**Render:** Google Chrome 152.0.7977.82; from the repository root:

```sh
google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1200,1760 \
  --default-background-color=00000000 \
  --screenshot=docs/diagrams/roles-and-channels-padded.png \
  "file://$PWD/docs/diagrams/roles-and-channels.svg"
python - <<'PYRENDER'
from PIL import Image
from pathlib import Path
padded = Path("docs/diagrams/roles-and-channels-padded.png")
with Image.open(padded) as image:
    assert image.size[0] == 2400 and image.size[1] >= 3120
    final = image.crop((0, 0, 2400, 3120)).convert("RGBA")
    assert final.getchannel("A").getextrema() == (255, 255)
    final.save("docs/diagrams/roles-and-channels.png")
padded.unlink()
PYRENDER
```

### Text equivalent

### Recommendation: assign authority

Name the accountable human, task agent, reviewer when the selected protocol requires one, and authorized publisher or delivery actor. Production alone grants no approval, publication, or installation authority. The human sets the applicable approval policy. Compatible responsibilities may share an actor; independent review retains the selected protocol's separation requirements.

### Documented: research verifier

For the research-team protocol, the independent verifier differs from the producer and may block publication. The reviewer box is explicitly conditional on that protocol; it is not an extra reviewer role alongside another review box.

Source: [specs/research-team-protocol.md:10-16](../../specs/research-team-protocol.md#L10-L16).

### Documented: ordinary artifact handoff

Read the deployment's destination, naming, and access rules; copy or upload to the documented destination; inspect existence and expected size or checksum; report the destination and verification evidence; stop on mismatch.

Source: [skills/fleet-operational-runbook/SKILL.md:11-15](../../skills/fleet-operational-runbook/SKILL.md#L11-L15).

### Recommendation: request and reply permissions

The requester writes the request and the recipient reads it. The responder writes the reply and the requester reads it. Specify each channel's permitted operations and verification point. Where isolation matters, configure and test actual access enforcement. Directory separation and a shared credential do not establish isolation. If enforcement is absent, label the separation “Procedural only.” Artifact delivery, a reply, and approval are separate operations.

### Recommendation: draw authorization separately

Draw a dotted arrow from the accountable human to the operation named by the selected approval policy. Its label begins “Authorization decision:”. Do not put it along the artifact arrow. This is a drawing recommendation, not a universal requirement for a fresh human approval on every publication.

### Documented: skill-installation scope

Before copying selected skills into an active skill directory, present the selected list, destinations, referenced scripts, and diff for human approval; inspect destination manifests and linked files after approval.

Source: [adopt/20_skills.md:49-53](../../adopt/20_skills.md#L49-L53).

### Documented: automation-change scope

Show the inventory, exact proposed files, command, rollback, and diff. Apply only the specifically approved change, then rerun the relevant probe.

Source: [adopt/40_protocols.md:54-58](../../adopt/40_protocols.md#L54-L58).

### Documented: curation scope

Verification precedes panel voting. The adopter selects ACCEPT or FILTER; disagreement retains dissent and requires an operator verdict. The minimum is two independent reviewer identities plus an accountable operator. Do not infer an additional actor-count rule for the verifier from this figure.

Source: [specs/curation-loop-architecture.md:102-110](../../specs/curation-loop-architecture.md#L102-L110), [specs/curation-loop-architecture.md:128-136](../../specs/curation-loop-architecture.md#L128-L136).

### Recommendation: optional recipe link

The optional box only points to the separately labelled inert-handoff recipe under Review and handoff. It states no inert end result. Ordinary destination verification does not establish inactivity. Use that recipe only after proving its prerequisites, or keep that expansion stopped and evaluate any standard deployment path under its own authorization and checks.

### Recommendation: local reading

and scoped stop

Handoff may be local; channels and stores are configured for actual need. Missing required role: gap, and this protocol stops. Another applicable workflow may be selected by the accountable human; the unmet gate is not a pass. This alternative cannot waive a project-mandated workflow. The visible short form is “Gap: required role missing / Stop this protocol; no pass”.

## Memory stores

R Apply only the selected workflow's rules.

<img src="memory-stores.png" width="700" alt="Standing rules and project instructions are distinct from episodic evidence. Current project documentation outranks an old note; other rule conflicts require the selected policy. Dashed examples show a pointer index, tool memory, secondary copies, reference storage, and an archive." />

[Full-size PNG](memory-stores.png) · [SVG source](memory-stores.svg)

**Alt text:** Standing rules and project instructions are distinct from episodic evidence. Current project documentation outranks an old note; other rule conflicts require the selected policy. Dashed examples show a pointer index, tool memory, secondary copies, reference storage, and an archive.

**Dimensions:** SVG 1200 × 1576; intended PNG 2400 × 3152. PNG dimensions and appearance await rendering.

**Render:** Google Chrome 152.0.7977.82; from the repository root:

```sh
google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1200,1776 \
  --default-background-color=00000000 \
  --screenshot=docs/diagrams/memory-stores-padded.png \
  "file://$PWD/docs/diagrams/memory-stores.svg"
python - <<'PYRENDER'
from PIL import Image
from pathlib import Path
padded = Path("docs/diagrams/memory-stores-padded.png")
with Image.open(padded) as image:
    assert image.size[0] == 2400 and image.size[1] >= 3152
    final = image.crop((0, 0, 2400, 3152)).convert("RGBA")
    assert final.getchannel("A").getextrema() == (255, 255)
    final.save("docs/diagrams/memory-stores.png")
padded.unlink()
PYRENDER
```

### Text equivalent

### Documented: durable and episodic purposes

Durable memory holds standing rules, decisions, preferences, and lasting facts; it is the authoritative memory tier. Episodic observations supply context and evidence, not binding rules. Search existing coverage before creating an entry. Keep index content as pointers, substantive content in entries, and avoid duplicating facts reliably represented in the repository unless a cross-session decision needs explanation.

Source: [skills/agent-memory-ops/SKILL.md:8-17](../../skills/agent-memory-ops/SKILL.md#L8-L17).

### Documented: current project instructions

Honor project-level safeguards and approvals. Current project documentation takes precedence when it conflicts with an old note. This source comparison concerns old notes, not every possible conflict between standing rules and project instructions.

Source: [skills/local-model-onboarding/SKILL.md:14](../../skills/local-model-onboarding/SKILL.md#L14), [skills/local-model-onboarding/SKILL.md:19-23](../../skills/local-model-onboarding/SKILL.md#L19-L23).

### Recommendation: depict precedence narrowly

Label the stores “Standing rules” and “Project instructions”; do not draw a universal winner between them. Follow the selected workflow's documented conflict rule; if it leaves a conflict unresolved, stop the dependent action and seek a ruling. This proposed stop is marked R, not a documented protocol minimum. The figure's precedence chip concerns only episodic notes versus current project documentation. An index is an optional example in this teaching layout: if present, its documented pointer-only rule applies. No required directory hierarchy follows from the layout.

### Recommendation: access and reading

State readers, writers, and actual loading behavior once in the shared access strip. Read applicable authoritative instructions; if required instructions cannot be read in full, stop the dependent action and report the gap. This proposed full-reading stop is marked R, not a documented protocol minimum.

### Recommendation: optional tool memory

Configure the loading and access the inspected tool actually provides. Do not assume automatic injection or visibility across tools. Tool memory may hold a maintained copy of selected rules; it is not a second authority. Its authority comes from the identified canonical rule, not the storage mechanism.

### Documented: conditional secondary-copy rule

Where canonical and secondary memory paths exist, confirm whether the intended relationship is a mirror or curated subset. Report content drift; choosing which version wins requires owner judgment. Do not change the arrangement without owner direction.

Source: [skills/brain-consistency-auditor/SKILL.md:69](../../skills/brain-consistency-auditor/SKILL.md#L69), [skills/brain-consistency-auditor/SKILL.md:146](../../skills/brain-consistency-auditor/SKILL.md#L146), [skills/brain-consistency-auditor/SKILL.md:154](../../skills/brain-consistency-auditor/SKILL.md#L154).

### Recommendation: optional reference stores

A shared reference store and an archive are examples, each with configured access, neither a new binding authority. An archive may be empty. Check current authority and any known replacement before using archived material; if status is unknown, do not use it as authority. A store read by an active loader is an active surface. A single-writer arrangement is an optional choice, not a general protocol rule.

### Recommendation: teaching examples and local reading

Dashed boxes are examples. The teaching figure retains the optional band; a deployment omits stores it does not have. Instructions and notes may be ordinary files with different purposes. Optional storage does not change the documented distinction between binding rules and episodic context.

## Review and handoff

R Apply only the selected workflow's rules.

<img src="review-and-handoff.png" width="700" alt="Selected workflow checks lead to candidate review and consumer-byte verification. Code, research, and curation retain separate documented requirements. Failure, cannot-check, or a missing required role leads to HOLD. The optional inert-handoff panel shows three prerequisites and a separately authorized installation boundary." />

[Full-size PNG](review-and-handoff.png) · [SVG source](review-and-handoff.svg)

**Alt text:** Selected workflow checks lead to candidate review and consumer-byte verification. Code, research, and curation retain separate documented requirements. Failure, cannot-check, or a missing required role leads to HOLD. The optional inert-handoff panel shows three prerequisites and a separately authorized installation boundary.

**Dimensions:** SVG 1200 × 2236; intended PNG 2400 × 4472. PNG dimensions and appearance await rendering.

**Render:** Google Chrome 152.0.7977.82; from the repository root:

```sh
google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1200,2436 \
  --default-background-color=00000000 \
  --screenshot=docs/diagrams/review-and-handoff-padded.png \
  "file://$PWD/docs/diagrams/review-and-handoff.svg"
python - <<'PYRENDER'
from PIL import Image
from pathlib import Path
padded = Path("docs/diagrams/review-and-handoff-padded.png")
with Image.open(padded) as image:
    assert image.size[0] == 2400 and image.size[1] >= 4472
    final = image.crop((0, 0, 2400, 4472)).convert("RGBA")
    assert final.getchannel("A").getextrema() == (255, 255)
    final.save("docs/diagrams/review-and-handoff.png")
padded.unlink()
PYRENDER
```

### Text equivalent

### Recommendation: common navigation, not a universal pipeline

Show “Scope”, “Applicable checks”, “Review candidate”, “Check consumed bytes”, then “Pass to authorized next step” or HOLD. This common navigation is R because the selected artifact workflow determines which reviews, checks, and next steps actually apply. The code sequence appears only in its D code card. Non-code artifacts use their own selected evidence checks. Add release checks only for actual release surfaces.

### Documented: code scope

For a code change that will be shipped, read project safeguards, scope verification before editing, use separate generation and review, repair, test the final reviewed result, and retain output. Use a distinct audit-oriented review model. Review is not a functional check; the generator cannot be its only reviewer.

Source: [skills/multi-agent-code-workflow/SKILL.md:9-21](../../skills/multi-agent-code-workflow/SKILL.md#L9-L21), [skills/multi-agent-code-workflow/SKILL.md:27-33](../../skills/multi-agent-code-workflow/SKILL.md#L27-L33); [skills/generate-review-fix-loop/SKILL.md:12-20](../../skills/generate-review-fix-loop/SKILL.md#L12-L20).

### Documented: research-team scope

Use at least two evidence legs with separate briefs; withhold peer results and the preferred conclusion from the independent leg. Retain artifacts and dissent. The verifier differs from the producer and may block publication. Agreement is not verification.

Source: [specs/research-team-protocol.md:10-16](../../specs/research-team-protocol.md#L10-L16), [specs/research-team-protocol.md:23-29](../../specs/research-team-protocol.md#L23-L29), [specs/research-team-protocol.md:38-42](../../specs/research-team-protocol.md#L38-L42).

### Documented: curation scope

Run the verifier before the panel vote. Require two independent reviewer identities plus an accountable operator. Apply the explicitly selected ACCEPT or FILTER policy; preserve disagreement and obtain an operator verdict. A required reviewer minimum is not satisfied by self-review or by substituting the operator for a reviewer. The figure does not add a separate headcount requirement for the verifier.

Source: [specs/curation-loop-architecture.md:102-110](../../specs/curation-loop-architecture.md#L102-L110), [specs/curation-loop-architecture.md:128-136](../../specs/curation-loop-architecture.md#L128-L136).

### Documented: factual splits

Resolve a factual split with retrieved ground truth or retain HOLD. Agreement does not verify a claim. This differs from curation's acceptance policy, which governs verified rule changes rather than voting a factual claim true.

Source: [skills/dual-model-reconciliation/SKILL.md:110-119](../../skills/dual-model-reconciliation/SKILL.md#L110-L119); [specs/research-team-protocol.md:13-15](../../specs/research-team-protocol.md#L13-L15); [specs/curation-loop-architecture.md:102-110](../../specs/curation-loop-architecture.md#L102-L110).

### Recommendation: cold-review recipe

Supply the same frozen candidate and contract through isolated inputs. Withhold peer reports and the preferred conclusion; retain each report before comparison. A review that has seen the discussion is a follow-up, not another cold review. Independent inputs and eligible reviewers both matter. The figure carries only the short R chip; this heading retains the recipe's recommendation status in the text equivalent.

### Documented: actual consumer input

For version-controlled publication, compare the snapshot that will be committed or published, with the same normalization applied by the relevant hashing process; working-copy hashes can miss line-ending transformations. For curation, approval binds to the exact input the applier consumes. If rebuilt instructions differ or their agreement cannot be established, re-audit before applying.

Source: [specs/rigor-spectrum.md:79-89](../../specs/rigor-spectrum.md#L79-L89); [specs/curation-loop-architecture.md:67-79](../../specs/curation-loop-architecture.md#L67-L79).

### Documented: unknown is not a pass

Refused and cannot-check outcomes both stop acceptance under the rigor guide. A research verifier unable to establish a claim records HOLD or the project's equivalent status.

Source: [specs/rigor-spectrum.md:11](../../specs/rigor-spectrum.md#L11), [specs/rigor-spectrum.md:34](../../specs/rigor-spectrum.md#L34); [specs/research-team-protocol.md:38-42](../../specs/research-team-protocol.md#L38-L42).

### Recommendation: shared stop notation

Fail, cannot-check, or a missing required role reaches HOLD; retain the candidate and evidence and stop publication or activation on that path. A repair makes a new candidate and returns to the affected checks and review. No changed candidate inherits stale acceptance. Missing required role: gap, and this protocol stops. Another applicable workflow may be selected by the accountable human; the unmet gate is not a pass, and project-mandated gates remain binding. Hash equality is a byte-identity check, not proof of meaning or inactivity.

### Documented: ordinary destination check

Follow documented destination, naming, and access rules, transfer the artifact, then inspect destination existence and expected size or checksum. Report evidence and stop on mismatch.

Source: [skills/fleet-operational-runbook/SKILL.md:11-15](../../skills/fleet-operational-runbook/SKILL.md#L11-L15).

### Optional recommendation: inert package handoff

This heading is the only home of the recipe. The ordinary path carries no inert or inactive end label. The roles figure links here without asserting an end state. Only this recommendation uses the end label “Verified inert; installation and activation need separate authorization.”

Prerequisites: required reviews and checks passed; transfer authorized; both temporary and final destinations verified outside all active loaders, watchers, updaters, and applying consumers. Prove this for the whole transfer and finalization interval, not only at a preflight instant. If any prerequisite is unknown, stop the inert expansion. A standard deployment procedure may be evaluated separately under its own authorization and checks; this is never an automatic bypass.

1. Freeze the reviewed candidate; record its hash and size.
2. Write it to a temporary destination name.
3. Verify the destination hash and size against the frozen candidate.
4. Finalize using an adopter-provided, verified atomic no-overwrite operation. A separate absence test followed by an ordinary rename is insufficient with concurrent writers.
5. Reread the final bytes and verify hash and size again.

Collision, unavailable atomic primitive, failed verification, or unproved inactivity stops this expansion and preserves evidence. A changed candidate returns to its required review and verification. Installation and activation require their separately selected authorization and verification procedure. The visible panel keeps all three prerequisite chips and its stop line; the ordered recipe remains here, outside the image.

Contrast only, not support for this recipe: the transaction specification describes atomic replacement and rollback. It does not supply this recommendation's no-overwrite finalization primitive. Do not draw that implementation contrast on the figure.

Contrast source: [guard/specs/SPEC_artifact_txn.md:13-17](../../guard/specs/SPEC_artifact_txn.md#L13-L17), [guard/specs/SPEC_artifact_txn.md:47-59](../../guard/specs/SPEC_artifact_txn.md#L47-L59).

### Recommendation: local reading

A handoff may use files in a local directory. Configure the permitted roles and operations; self-review does not fill an independent-review gate. Select a feasible applicable workflow without recasting a missing required gate as a pass.

## Source and placement notes

Source links on individual items identify their evidence ranges. Entry-point links elsewhere
in the documentation identify useful reading locations; they do not replace those evidence ranges.
The ordinary handoff procedure and the optional inert package recipe retain separate scopes.


## Reading the connectors

Historical routing notes: this section and “Revised routing and layout” retain earlier descriptions.
Use [Current routing clarification](#current-routing-clarification) for the revised figures.

The roles figure shows selected request, reply, delivery-role, and authorization relationships.
The text equivalent specifies the permitted operations and all handoff checks; omitted routes do
not remove those requirements. Authorization arrows remain separate from information flow.

In the memory figure, the solid arrow means a pointer reference: the optional index points to
standing rules. “maintained copy” describes the optional secondary-copy card. No arrow chooses a
canonical source for that copy; the selected configuration determines its source and relationship.
Read access follows configured policy and is not represented by a second arrow meaning.

The review flow runs from Scope to Applicable checks, then Review candidate and Check consumed
bytes. The check leads to Pass or HOLD. The HOLD-to-Repair return reaches Applicable checks and
repeats review before checking the new candidate. Earlier required checks also stop on failure or
cannot-check, as specified in the text equivalent. The protocol key below the flow retains its
separate scopes; it is not a continuation of the arrow sequence.

### Revised routing and layout

In Roles and channels, Request and Reply describe the adjacent down and up arrows between
Task agent and the conditional Research verifier. They are channel descriptions, not extra actors.
The separate artifact route runs from Task agent through Authorized publisher to Artifact handoff.
The dotted example runs from Accountable human through Authorization decision to the Task agent
boundary; the selected policy determines the actual authorization target in a deployment.

In Memory stores, the unlabelled port beside the secondary copy represents the canonical source
selected by configuration. The `maintained copy` arrow ends at the copy card. The teaching figure
does not select Standing rules, Project instructions, or another named box as that source.
This restores the connector; the earlier reading note's single-arrow description is superseded.

Each source canvas ends 40 pixels after its final card. The optional review frame has 26 pixels
between its geometric boundary and the inner cards (24.5 pixels between the painted strokes).


### Current routing clarification

This clarification supersedes the earlier connector and adjacent-arrow readings above. Request and Reply are
channel cards on their respective routes: Task agent → Request → Research verifier, and Research
verifier → Reply → Task agent. The two segments depict writing and reading the same channel;
they add no actor or approval step. The Channel separation chip sits between both channel cards.

Applicable checks, Review candidate, and Check consumed bytes each have a failure route to the
single HOLD card. Applicable checks is a gate when the selected workflow requires those checks:
selection determines applicability, and an unsuccessful or unknown required result stops acceptance.
The existing card labels name the navigation and HOLD outcome; no connector labels are added.

In Memory stores, both solid arrows are labelled relationships: `points to` and `maintained copy`.
The source dot is separated from every named store; configuration supplies the canonical source.
A port is not an arrowhead, and the teaching figure assigns no named store as the copy's source.
