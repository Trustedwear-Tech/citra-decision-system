"""
Security helpers
================
Validation and escaping primitives for untrusted identifiers and string
literals that flow into Milvus boolean expressions, SQL identifiers, etc.

Why this exists
---------------
The Milvus boolean expression language uses double-quoted string literals.
Naïvely interpolating a user/source-controlled value with only `"` escaping
is bypassable — e.g. a value ending in a single backslash escapes the
closing quote, allowing the rest of the expression to be hijacked.

Two patterns:

  1.  Identifiers (source_id, table_name, collection_name) — drawn from a
      restricted alphabet at registration / ingest time. Validate with
      `assert_safe_ident()` so any malformed value raises immediately,
      rather than silently flowing into a filter string.

  2.  Free-form values (file paths used in `source ==` filters) — these
      legitimately contain backslashes/quotes. Use `milvus_str_literal()`
      which escapes BOTH the backslash and the double quote in the order
      Milvus expects, then wraps in quotes.
"""

import re
from typing import Final

# Strict identifier alphabet: alphanumerics, underscore, hyphen, dot.
# Mirrors what we accept as a Milvus collection-name suffix and what we
# allow as a SQL/Mongo table name post-introspection.
_SAFE_IDENT_RE: Final = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


class UnsafeIdentifierError(ValueError):
    """Raised when an identifier fails the safe-identifier check."""


def assert_safe_ident(value: str, *, kind: str = "identifier") -> str:
    """
    Validate that `value` is a safe identifier.

    Returns `value` unchanged on success so callers can chain. Raises
    `UnsafeIdentifierError` on failure with a descriptive message.
    """
    if not isinstance(value, str) or not _SAFE_IDENT_RE.match(value):
        raise UnsafeIdentifierError(
            f"Refusing unsafe {kind}: {value!r}. "
            f"Must match {_SAFE_IDENT_RE.pattern}."
        )
    return value


def milvus_str_literal(value: str) -> str:
    """
    Escape and quote `value` so it can be safely embedded as a string
    literal in a Milvus filter expression.

    Order of replacements matters: backslash first, then double quote.
    """
    if value is None:
        return '""'
    s = str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
