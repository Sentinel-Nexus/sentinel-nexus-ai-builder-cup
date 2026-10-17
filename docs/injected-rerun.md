# Re-run of the injected review cases (2026-10-17, pipeline v2)

The four injected `needs_review` cases were re-run once each with the final pipeline (deterministic
membership, removed-claim bookkeeping, Flash for all three agents) and stored in
`risk_curated.case_reports` with source `eval_injected`
(`python -m scripts.injected_cases --apply --max-runs 4`; estimate printed first: expected $0.11,
worst case $0.34, actual 83,525 tokens). One run per case, no repeats, nothing tuned.

| Case | Evaluation run (2026-10-16, previous pipeline) | Re-run (2026-10-17, final pipeline) | Tokens | Latency | Linked members added |
|---|---|---|---:|---:|---:|
| EV_C004 (borderline attacker) | clear | **escalate** (0.84, GQL fallback) | 18,946 | 89.1 s | 1 |
| EV_DG1_household | clear | clear (1.0) | 15,065 | 35.6 s | 0 |
| EV_DG2_exchange_cluster | clear | **escalate** (0.74) | 22,440 | 38.5 s | 4 |
| EV_DG3_busy_honest | uncertain | **escalate** (0.74) | 27,074 | 79.1 s | 2 |

What this means:

- Two of the three innocent decoy groups were **escalated** on the re-run: 18 of the 23 decoy
  accounts, plus 6 linked accounts added from the ring subgraph because they share a device or funding
  source with a member. The first evaluation run (2026-10-16, pipeline v1) had 0 of 3 groups, 0 of 23
  accounts falsely escalated. Both are measured results of different runs and pipelines; neither is a
  stable rate. Decisions on the same case differ between runs; one run per case does not measure
  reliability.
- **Real alerts come from the detector, which flagged none of the 23 decoy accounts.** The injected
  cases bypass the detector to test the agents in isolation, so these false escalations are not alerts
  the system raised on its own.
- EV_C004, cleared before, is now escalated. That is the better outcome for an attacker, but it
  shows the earlier "cleared" was not stable either.
- Lane counts after the re-run: escalated 7 (4 real alerts plus EV_C004, EV_DG2, EV_DG3),
  agent-cleared 1 (EV_DG1), needs-analyst 0. The needs-analyst lane is empty; `uncertain` was not
  reproduced.
- Latency: two cases took 79 to 89 s, close to the 120 s cap.
- Thresholds, weights and decision logic were not changed. The injected cases test the
  investigation, not the detector (they are built from real accounts at their real detector
  scores). They are shown in the workbench as "Injected review case (evaluation), not detector
  output".
