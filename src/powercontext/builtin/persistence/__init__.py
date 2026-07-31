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

__all__ = (
    "AsyncDatabase",
    "CandidateRepository",
    "DatabaseClosedError",
    "GenerationConflictError",
    "IdentityMismatchError",
    "InvalidRepositoryArgumentError",
    "InvalidStoredColumnError",
    "InvalidStoredPayloadError",
    "PersistenceError",
    "RepositoryError",
    "RepositoryNotFoundError",
    "StoredPayloadConflictError",
)
