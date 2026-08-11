"""Credential redaction at the replay evidence boundary."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlsplit

REDACTED = "[REDACTED]"

_SENSITIVE_ENVIRONMENT_NAMES = frozenset({
    "api_key",
    "authorization",
    "credentials",
    "database_url",
    "mysql_pwd",
    "password",
    "pgpassword",
    "private_key",
    "secret",
    "token",
})
_SENSITIVE_ENVIRONMENT_SUFFIXES = (
    "_access_key_id",
    "_api_key",
    "_authorization",
    "_client_secret",
    "_credentials",
    "_database_url",
    "_dsn",
    "_password",
    "_private_key",
    "_secret",
    "_secret_access_key",
    "_secret_key",
    "_token",
)
_SENSITIVE_FIELD_NAMES = frozenset({
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "client_secret",
    "connection_string",
    "credentials",
    "database_url",
    "dsn",
    "password",
    "private_key",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
})
_SENSITIVE_FIELD_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_auth_token",
    "_authorization",
    "_client_secret",
    "_connection_string",
    "_credentials",
    "_database_url",
    "_dsn",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_secret_key",
)
_DATABASE_URL = re.compile(
    r"(?i)\b(?:cockroachdb|mariadb|mssql|mysql|oceanbase|oracle|postgres|postgresql|sqlite)"
    r"(?:\+[a-z0-9_.-]+)?://[^\s\"'`<>{}\[\](),;\\]+"
)
_URL_PASSWORD = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@/\s\"'`<>]+(@)")
_URL_QUERY_CREDENTIAL = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?key|auth[_-]?token|client[_-]?secret|password|secret|token)=)"
    r"[^&#\s\"'`]+"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:access[_-]?token|api[_-]?key|auth[_-]?token|authorization|client[_-]?secret|password|secret)"
    r"\b\s*[:=]\s*)(?:(?:basic|bearer)\s+)?[^\s,;&\"'`]+"
)
_CREDENTIAL_OPTION = re.compile(
    r"(?i)(--(?:access-token|api-key|auth-token|client-secret|password|token)\s+)"
    r"[^\s,;\"'`]+"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{16,}")
_PROVIDER_CREDENTIAL = re.compile(r"(?<![a-zA-Z0-9_-])sk-[a-zA-Z0-9_-]{8,}(?![a-zA-Z0-9_-])")


@dataclass(frozen=True)
class EvidenceRedactor:
    """Remove configured and structurally recognizable credentials from evidence."""

    secrets: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> EvidenceRedactor:
        secrets: set[str] = set()
        for name, value in os.environ.items():
            if value and _is_sensitive_environment_name(name):
                secrets.update(_secret_variants(value))
        return cls(tuple(sorted(secrets, key=lambda item: (-len(item), item))))

    def redact(self, value: Any) -> Any:
        """Recursively redact JSON-compatible evidence without changing its normal shape."""
        return self._redact(value, sensitive=False)

    def redact_text(self, value: str) -> str:
        """Redact one final serialized evidence value."""
        for secret in self.secrets:
            if len(secret) >= 8:
                value = value.replace(secret, REDACTED)
            else:
                value = re.sub(rf"(?<![a-zA-Z0-9]){re.escape(secret)}(?![a-zA-Z0-9])", REDACTED, value)
        value = _DATABASE_URL.sub(REDACTED, value)
        value = _URL_PASSWORD.sub(rf"\1{REDACTED}\2", value)
        value = _URL_QUERY_CREDENTIAL.sub(rf"\1{REDACTED}", value)
        value = _CREDENTIAL_ASSIGNMENT.sub(rf"\1{REDACTED}", value)
        value = _CREDENTIAL_OPTION.sub(rf"\1{REDACTED}", value)
        value = _PROVIDER_CREDENTIAL.sub(REDACTED, value)
        return _BEARER_CREDENTIAL.sub(REDACTED, value)

    def _redact(self, value: Any, *, sensitive: bool) -> Any:
        if isinstance(value, str):
            return REDACTED if sensitive and value else self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                self.redact_text(key) if isinstance(key, str) else key: self._redact(
                    item,
                    sensitive=sensitive or (isinstance(key, str) and _is_sensitive_field_name(key)),
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item, sensitive=sensitive) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact(item, sensitive=sensitive) for item in value)
        return value


def _is_sensitive_environment_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return normalized in _SENSITIVE_ENVIRONMENT_NAMES or normalized.endswith(_SENSITIVE_ENVIRONMENT_SUFFIXES)


def _is_sensitive_field_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith(_SENSITIVE_FIELD_SUFFIXES)


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _secret_variants(value: str) -> set[str]:
    variants = {value}
    stripped = value.strip()
    if stripped:
        variants.add(stripped)
    lowered = stripped.casefold()
    for prefix in ("basic ", "bearer "):
        if lowered.startswith(prefix):
            variants.add(stripped[len(prefix) :])
    variants.update(_url_secret_variants(stripped))
    for item in tuple(variants):
        if item:
            variants.add(quote(item, safe=""))
    return {item for item in variants if item}


def _url_secret_variants(value: str) -> set[str]:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return set()
    if not parsed.netloc:
        return set()
    variants = set()
    if parsed.password:
        variants.update((parsed.password, unquote(parsed.password)))
    for name, item in parse_qsl(parsed.query, keep_blank_values=True):
        if item and _is_sensitive_field_name(name):
            variants.update((item, unquote(item)))
    return variants
