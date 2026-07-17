from __future__ import annotations


class LimitError(Exception):
    """Base class for typed Limits domain failures."""


class LimitNotFoundError(LimitError):
    pass


class LimitValidationError(LimitError):
    pass


class LimitConflictError(LimitError):
    pass


class LimitImmutableError(LimitConflictError):
    pass
