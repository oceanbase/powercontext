"""SQLAlchemy-backed relational persistence building blocks."""

from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import (
    DatabaseClosedError,
    GenerationConflictError,
    IdentityMismatchError,
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    PersistenceError,
    RepositoryError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.external_skills import ExternalSkillRepository
from powercontext.builtin.persistence.statistics import (
    StatisticsRepository,
    StoredInventoryCounts,
    StoredModelUsage,
    StoredRecallTokenUsage,
)

__all__ = (
    "AsyncDatabase",
    "CandidateRepository",
    "DatabaseClosedError",
    "ExternalSkillRepository",
    "GenerationConflictError",
    "IdentityMismatchError",
    "InvalidRepositoryArgumentError",
    "InvalidStoredColumnError",
    "InvalidStoredPayloadError",
    "PersistenceError",
    "RepositoryError",
    "RepositoryNotFoundError",
    "StatisticsRepository",
    "StoredInventoryCounts",
    "StoredModelUsage",
    "StoredPayloadConflictError",
    "StoredRecallTokenUsage",
)
