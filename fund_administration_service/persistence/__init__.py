"""Persistence-store protocol + reference in-memory implementation."""

from fund_administration_service.persistence.in_memory_store import (
    InMemoryStore,
    PersistenceStore,
)

__all__ = ["InMemoryStore", "PersistenceStore"]
