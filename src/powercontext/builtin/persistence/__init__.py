"""SQLAlchemy-backed relational persistence building blocks."""

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
