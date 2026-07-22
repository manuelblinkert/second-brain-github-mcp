import pytest

from registry import (
    Member,
    MemberRegistry,
    RegistryConfigError,
    load_registry_from_env,
)


def _member(mid):
    return Member(
        member_id=mid,
        username=mid,
        password=f"pw-{mid}",
        github_token=f"tok-{mid}",
    )


def test_authenticate_returns_member_on_exact_match():
    reg = MemberRegistry([_member("alice"), _member("bob")])
    m = reg.authenticate("bob", "pw-bob")
    assert m is not None and m.member_id == "bob"


def test_authenticate_rejects_wrong_password():
    reg = MemberRegistry([_member("alice")])
    assert reg.authenticate("alice", "wrong") is None


def test_authenticate_rejects_unknown_username():
    reg = MemberRegistry([_member("alice")])
    assert reg.authenticate("ghost", "pw-alice") is None


def test_authenticate_handles_non_ascii_without_crashing():
    reg = MemberRegistry([_member("alice")])
    # A non-ASCII username/password must return None (no match), not raise.
    assert reg.authenticate("naïve-Ünïcode", "påsswörd") is None


def test_authenticate_matches_non_ascii_credentials():
    reg = MemberRegistry([
        Member("u1", "naïve", "påss-wörd", "tok-u1"),
    ])
    m = reg.authenticate("naïve", "påss-wörd")
    assert m is not None and m.member_id == "u1"


def test_by_id():
    reg = MemberRegistry([_member("alice")])
    assert reg.by_id("alice").github_token == "tok-alice"
    assert reg.by_id("nope") is None


def test_load_from_env_builds_all_members():
    env = {
        "TEAM_MEMBER_IDS": "alice, bob",
        "MEMBER_ALICE_USERNAME": "alice",
        "MEMBER_ALICE_PASSWORD": "pw1",
        "MEMBER_ALICE_GITHUB_TOKEN": "tok1",
        "MEMBER_BOB_USERNAME": "bob",
        "MEMBER_BOB_PASSWORD": "pw2",
        "MEMBER_BOB_GITHUB_TOKEN": "tok2",
    }
    reg = load_registry_from_env(env)
    assert {m.member_id for m in reg.members()} == {"alice", "bob"}
    assert reg.by_id("bob").github_token == "tok2"


def test_load_from_env_rejects_missing_field():
    env = {
        "TEAM_MEMBER_IDS": "alice",
        "MEMBER_ALICE_USERNAME": "alice",
        "MEMBER_ALICE_PASSWORD": "pw1",
        # MEMBER_ALICE_GITHUB_TOKEN missing
    }
    with pytest.raises(RegistryConfigError):
        load_registry_from_env(env)


def test_load_from_env_rejects_empty_roster():
    with pytest.raises(RegistryConfigError):
        load_registry_from_env({"TEAM_MEMBER_IDS": "  "})
