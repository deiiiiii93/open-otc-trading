"""Block producers, imported for their registration side effects.

Importing this package registers every block. Anything that resolves blocks
must import it first, or the registry will be empty.
"""
from __future__ import annotations

from . import desk, limits, pnl, risk  # noqa: F401

__all__ = ["risk", "pnl", "limits", "desk"]
