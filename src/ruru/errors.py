"""Errors that map onto the CLI's exit codes (README, the agent contract)."""

from __future__ import annotations

OK = 0
USAGE = 1  # usage error, or an unknown key or ID
NO_MATCH = 2  # nothing matched (an empty search, `check` found issues)
PARTIAL = 3  # some feeds or resolvers failed
LOCKED = 4  # the library lock is held by another ruru process


class RuruError(Exception):
    code = USAGE


class UsageError(RuruError):
    code = USAGE


class UnknownKey(RuruError):
    code = USAGE

    def __init__(self, key: str) -> None:
        super().__init__(f"no paper with key {key!r}")
        self.key = key


class NothingMatched(RuruError):
    code = NO_MATCH


class LockHeld(RuruError):
    code = LOCKED
