"""Team member registry: maps login credentials to per-member GitHub PATs.

This is the identity allowlist for the team server. Only members listed in
TEAM_MEMBER_IDS (with a complete MEMBER_<ID>_* block) can ever authenticate,
and each member's OAuth subject is their member id. The per-member PAT is read
only here and on the GitHub client — never sent to or shown to Claude.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field


class RegistryConfigError(ValueError):
    """Raised when the team-member env configuration is missing or malformed."""


@dataclass(frozen=True)
class Member:
    member_id: str
    username: str
    password: str = field(repr=False)
    github_token: str = field(repr=False)


class MemberRegistry:
    """Immutable lookup of team members by username (login) and by id."""

    def __init__(self, members: list[Member]) -> None:
        if not members:
            raise RegistryConfigError("No team members configured.")
        self._members = list(members)
        self._by_id = {m.member_id: m for m in members}

    def members(self) -> list[Member]:
        return list(self._members)

    def by_id(self, member_id: str) -> Member | None:
        return self._by_id.get(member_id)

    def authenticate(self, username: str, password: str) -> Member | None:
        """Return the matching member, or None. Constant-time over all members.

        Iterates every member without short-circuiting so that neither a wrong
        username nor a wrong password is faster to reject than the other.
        """
        matched: Member | None = None
        for member in self._members:
            user_ok = secrets.compare_digest(
                username.encode("utf-8"), member.username.encode("utf-8")
            )
            pass_ok = secrets.compare_digest(
                password.encode("utf-8"), member.password.encode("utf-8")
            )
            if user_ok and pass_ok:
                matched = member
        return matched


_REQUIRED_FIELDS = ("USERNAME", "PASSWORD", "GITHUB_TOKEN")


def load_registry_from_env(env: Mapping[str, str] | None = None) -> MemberRegistry:
    """Build a MemberRegistry from TEAM_MEMBER_IDS + MEMBER_<ID>_* env vars."""
    import os

    source: Mapping[str, str] = env if env is not None else os.environ

    raw_ids = source.get("TEAM_MEMBER_IDS", "")
    member_ids = [part.strip() for part in raw_ids.split(",") if part.strip()]
    if not member_ids:
        raise RegistryConfigError("TEAM_MEMBER_IDS is empty or unset.")

    members: list[Member] = []
    for member_id in member_ids:
        prefix = f"MEMBER_{member_id.upper()}_"
        values: dict[str, str] = {}
        for field in _REQUIRED_FIELDS:
            key = prefix + field
            value = source.get(key, "")
            if not value or not value.strip():
                raise RegistryConfigError(f"Missing or blank {key}.")
            values[field] = value.strip()
        members.append(
            Member(
                member_id=member_id,
                username=values["USERNAME"],
                password=values["PASSWORD"],
                github_token=values["GITHUB_TOKEN"],
            )
        )

    return MemberRegistry(members)
