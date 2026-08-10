from __future__ import annotations


class SettlementError(Exception):
    """Base class for typed Settlement domain failures."""


class SettlementNotFoundError(SettlementError):
    pass


class SettlementValidationError(SettlementError):
    pass


class SettlementConflictError(SettlementError):
    pass
