"""guard — drift / mutation guards for the video-research pipeline.

Adapted from the ParaKit `_breaker/` stack (a peer agent, 2026-07-31). The transferable part turned out to
be the meta-harness — the machinery that keeps invariants honest — not the invariants themselves.

  research_properties.py  artifact -> deterministic property vector (no stored baseline; see the reply)
  teeth_prover.py         proves every guard can actually fail (build+run this FIRST)
  contract_agreement.py   N surfaces must agree on one contract, pre-dispatch
  stage_ledger.py         in == out + dropped_with_reason; UNMEASURED is loud
  leg_canary.py           an unexercised component must be asserted alive on a schedule
  artifact_txn.py         mutate-in-place artifacts get a transaction with rollback
"""
