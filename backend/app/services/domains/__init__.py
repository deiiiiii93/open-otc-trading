"""Pure-Python service facade modules.

Each module re-exposes a domain's operations as pure functions returning ORM
objects or primitives. Tool wrappers in app/tools/ and CLI commands in app/cli/
both call into this layer.
"""
