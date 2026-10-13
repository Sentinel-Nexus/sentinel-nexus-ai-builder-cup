"""Deterministic synthetic trading data with labeled manipulation scenarios."""

from services.ingest.synth.generator import SyntheticData, generate, write_dataset

__all__ = ["SyntheticData", "generate", "write_dataset"]
