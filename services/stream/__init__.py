"""Near-real-time micro-batch streaming path (P6): Pub/Sub -> BigQuery -> frozen detector.

Feature-flagged (``STREAMING_ENABLED``, off by default). Nothing here changes the detector: scoring
runs the repo's frozen SQL (sql/views) and weights (services/detector) over the stream tables.
"""
