"""Everything one service instance shares between requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from services.app.agents import Backend
from services.app.audit import AuditSink
from services.app.cases import CaseStore
from services.app.config import Settings
from services.app.data import DataStore
from services.app.policy import PolicyIndex
from services.app.ratelimit import ForceLimiter


@dataclass
class Deps:
    settings: Settings
    store: DataStore
    graph: object  # services.graph.service.GraphService
    policy: PolicyIndex
    backend: Backend
    cases: CaseStore
    audit_factory: Callable[[], AuditSink]
    limiter: ForceLimiter
