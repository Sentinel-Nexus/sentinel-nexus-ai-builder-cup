# ADR 0002: Decoy groups and link evasion are generated, not patched in

Status: accepted, 2026-10-16

## Context

The live data carried hard cases that only existed as `UPDATE` statements in
`sql/schema/harden.sql` (three innocent groups; shared funding removed from part of each
attack). They were not reproducible from the generator, they were keyed to account-id ranges,
and the innocent groups shared both a device and a funding source, so they were also the
easiest thing for a detector to learn by id range. P2 needs an evaluation set where a flat
rule cannot score near 100% and where false positives are measurable by group.

## Decision

`services/ingest/synth/generator.py` plants them at generation time, from a separate RNG
(`Random(f"{seed}:decoys")`) that runs last and only appends rows. Rows and ids that existed
under ADR 0001 are unchanged (a test pins this). No SQL patching.

Evasion. For each scenario, 40% of the attackers (chosen by the decoy RNG) stop sharing
a funding source (`FE_<account>`, unique):

| Scenario | Evaded | Still linked through |
|---|---|---|
| S1 wash ring (8) | 3 | devices `DW1/DW2`, matched fills |
| S2 pre-resolution (6) | 2 | a shared device `DC_EV`, same market and late timing |
| S3 mule (25) | 10 | devices `DM1..DM3` |

Decoys, each suspicious on exactly one signal and recorded in `eval.decoy_groups`:

| Group | Accounts | Looks like | Why it is innocent |
|---|---|---|---|
| DG1 household | 5 (`A0100-A0104`) | shared device | different funding, aged accounts, ordinary trading |
| DG2 exchange cluster | 8 (`A0200-A0207`) | shared funding source | one exchange withdrawal wallet (`FEX1`); aged accounts, different devices |
| DG3 busy but honest | 10 (`A0300-A0309`) | heavy buying in the last hours | aged accounts, no shared device or funding, mixed sizes (lognormal, cap $3,000), 15% opposite side |

The 39 attacker ground-truth accounts are unchanged (8 wash, 6 coordination, 25 mule).
`eval.decoy_groups` is used only by evaluation. The detector must not read it or
`eval.ground_truth`; this is enforced by a unit test over the detector SQL and code.

## Consequences

- Trades: 35,073 -> 35,116 (43 decoy trades appended). Accounts and markets unchanged.
- `sql/schema/harden.sql` is deleted. Reproduce live with generate/load, then `market_graph.sql`.
- The flat rule "funding source shared by 5 or more accounts" now flags 20 of 39 attackers
  (wash 5/8, coordination 0/6, mule 15/25) and all 8 members of the exchange cluster.
- Live timestamps still differ from a rerun by a constant (ADR 0001); structure is identical.
