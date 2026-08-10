---
name: fleet-operational-runbook
description: Perform routine handoffs and file operations with an explicit destination check after each action.
license: MIT
---
# Fleet Operational Runbook

## Overview
Use a deployment's documented handoff channel for routine cross-machine or cross-team work. Prefer the simplest auditable transfer path and verify the destination before considering any operation evidenced.

## Procedure: publish a handoff artifact
1. Read the destination, naming, and access rules from project documentation.
2. Copy or upload the artifact to the documented handoff location.
3. Inspect the destination for existence and expected size or checksum.
4. Report the exact destination and verification evidence; stop on a mismatch.

## Procedure: request work on another machine
1. Write a self-contained instruction artifact: goal, inputs, ordered steps, expected output, verification method, and requested response.
2. Send it through the documented handoff channel.
3. Verify that the recipient-side location contains the instruction before reporting the handoff.

## Rules
- Quote paths that can contain spaces or shell-special characters.
- Do not directly control a remote environment when the deployment requires an auditable handoff instead.
- Name research legs and artifacts according to the project convention so reconciliation can attribute them.

## Verification checklist
- [ ] The destination was inspected after transfer.
- [ ] Naming and destination rules were followed.
- [ ] Any cross-machine work includes self-contained instructions.
