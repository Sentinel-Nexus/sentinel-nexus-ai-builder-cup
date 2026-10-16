# services/graph

Graph tools over `risk_graph.market_graph`, for the agents (P4) and the web workbench (P5).

```python
from services.graph.service import GraphService

svc = GraphService(project)
svc.neighbors("A0100", hops=2)  # accounts within 2 link hops, with the edges seen
svc.ring_subgraph("R_W001")  # whole connected ring: members, nodes, edges (JSON)
svc.trades_for(["W001"], "M05")  # TradedIn summary edges + trade rows (<= 500)
```

Every reply has `engine` (`gql` or `fallback`), `cached`, and, after a fallback,
`fallback_reason`. Set `GRAPH_FORCE_FALLBACK=1` to force the fallback.

- **GQL first.** One-hop GQL queries on `market_graph` (UsesDevice, FundedBy, TradedWith) per
  frontier, three in parallel. 15 s budget per call. Any error, timeout or unavailable
  reservation answers from the fallback and opens a 60 s breaker.
- **Fallback.** Plain SQL reads the three edge tables once (cached 5 min); NetworkX does the
  traversal. Same hub rule as the detector (a device or funding source shared by more than 25
  accounts links nothing), same results.
- **Ids.** Nodes are `Account:<id>`, `Device:<id>`, `FundingSource:<id>`. Edges are
  `UsesDevice:<account>><device>`, `FundedBy:...`, `TradedWith:<seller>><buyer>`,
  `TradedIn:<account>><market>`: the same format as the detector's `evidence_edge_ids`.
  `ring_id` is `R_<lowest account id of the ring>`.
- **Safety.** `runner.py` is the only place a job is created: read-only guard (one SELECT or
  GRAPH statement), `maximum_bytes_billed` 100 MB and a timeout on every job; values only
  travel as query parameters.
- **Cost.** Warm GQL calls take about 3-10 s (one round trip per hop). The fallback is
  instant after its first load (about 9 s cold). Call `warm()` at start-up.

Tests: `tests/unit/test_graph_*.py` (fake BigQuery over the seed-42 data) and
`GRAPH_LIVE_TESTS=1 pytest tests/integration/test_graph_live.py` (real GQL).
