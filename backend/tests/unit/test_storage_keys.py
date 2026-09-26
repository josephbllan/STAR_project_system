"""Storage-key construction, validation, and the signed grant.

No filesystem here: these functions are arithmetic over strings and a signature over a payload.
The operations that write bytes are in `integration/`.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from apps.common import storage

pytestmark = pytest.mark.unit

PUBLIC_ID = UUID("3f2b1c9a-0000-4000-8000-0123456789ab")


def test_key_is_derived_from_the_public_id() -> None:
    """Derived from an identifier the application generated, never from a
 client-supplied filename."""
    key = storage.storage_key_for_content(PUBLIC_ID)
    assert key == "evidence/3f/2b/3f2b1c9a0000400080000123456789ab"


def test_keys_fan_out_so_no_directory_holds_everything() -> None:
    first = storage.storage_key_for_content(UUID(int=1))
    second = storage.storage_key_for_content(UUID(int=2 << 120))
    assert first.split("/")[1:3] != second.split("/")[1:3]


def test_derived_artifact_kind_is_part_of_the_path() -> None:
    key = storage.storage_key_for_artifact(PUBLIC_ID, "thumbnail")
    assert key.startswith("derived/thumbnail/")


def test_unknown_artifact_kind_is_refused() -> None:
    """The kind becomes a path segment, so an arbitrary string would be a traversal vector."""
    with pytest.raises(storage.InvalidStorageKeyError):
        storage.storage_key_for_artifact(PUBLIC_ID, "../../etc")


def test_report_key_is_distinct_from_evidence() -> None:
    """Reports expire and are deleted; original evidence never is. Sharing a prefix would let an
 expiry sweep's predicate reach evidence."""
    assert storage.storage_key_for_report(PUBLIC_ID).startswith("reports/")


@pytest.mark.parametrize(
    "key",
    [
        "../../../etc/passwd",
        "evidence/../../secret",
        "/absolute/path",
        "C:\\windows\\system32",
        "evidence//doubled",
        "evidence/trailing/",
        "Evidence/Upper/Case",
        "evidence/with space",
        "evidence/semi;colon",
        "",
        "x" * 513,
    ],
)
def test_malformed_keys_are_refused(key: str) -> None:
    """: prevented by construction rather than by validation alone. Every entry point
 re-validates, including keys arriving from a database row, because a row is only as
 trustworthy as whatever last wrote to it."""
    with pytest.raises(storage.InvalidStorageKeyError):
        storage.validate_key(key)


def test_generated_keys_pass_their_own_validator() -> None:
    """The constructors and the validator must agree. If they drift, every write fails at
 runtime and no unit test would have said which side was wrong."""
    storage.validate_key(storage.storage_key_for_content(PUBLIC_ID))
    storage.validate_key(storage.storage_key_for_artifact(PUBLIC_ID, "crop"))
    storage.validate_key(storage.storage_key_for_report(PUBLIC_ID))


def test_signed_token_round_trips_to_the_same_key() -> None:
    key = storage.storage_key_for_content(PUBLIC_ID)
    access = storage.signed_url(key)
    token = access.url.rsplit("/", 1)[-1]
    assert storage.resolve_access_token(token) == key


def test_token_is_opaque_and_url_safe() -> None:
    """The key's slashes must not reach the URL: a `path` converter would be needed on the
 route, and the storage layout would appear in every server log."""
    access = storage.signed_url(storage.storage_key_for_content(PUBLIC_ID))
    token = access.url.rsplit("/", 1)[-1]
    assert "/" not in token
    assert "evidence" not in token


def test_altered_token_is_refused() -> None:
    access = storage.signed_url(storage.storage_key_for_content(PUBLIC_ID))
    token = access.url.rsplit("/", 1)[-1]
    with pytest.raises(storage.InvalidAccessTokenError):
        storage.resolve_access_token(token[:-1] + ("a" if token[-1] != "a" else "b"))


def test_unsigned_token_is_refused() -> None:
    with pytest.raises(storage.InvalidAccessTokenError):
        storage.resolve_access_token("evidence/3f/2b/3f2b:9999999999")


def test_expired_token_is_refused() -> None:
    """A grant already issued cannot be extended by its holder."""
    access = storage.signed_url(
        storage.storage_key_for_content(PUBLIC_ID), ttl=timedelta(seconds=-1)
    )
    token = access.url.rsplit("/", 1)[-1]
    with pytest.raises(storage.InvalidAccessTokenError):
        storage.resolve_access_token(token)


def test_failure_does_not_say_how_it_failed() -> None:
    """An altered token and an expired one produce the same message, because
 distinguishing them would tell a holder which half to attack."""
    expired = storage.signed_url(
        storage.storage_key_for_content(PUBLIC_ID), ttl=timedelta(seconds=-1)
    ).url.rsplit("/", 1)[-1]

    messages = set()
    for token in (expired, "not-a-token-at-all"):
        with pytest.raises(storage.InvalidAccessTokenError) as caught:
            storage.resolve_access_token(token)
        messages.add(str(caught.value))
    assert len(messages) == 1


def test_expiry_is_fixed_at_issue_not_at_validation(settings) -> None:
    """The lifetime is decided when authorisation was evaluated. Shortening the configured value
 afterwards must not invalidate a grant already issued, and lengthening it must not extend
 one - which is why the expiry travels inside the signed payload."""
    settings.SIGNED_URL_TTL_SECONDS = 3600
    token = storage.signed_url(storage.storage_key_for_content(PUBLIC_ID)).url.rsplit("/", 1)[-1]
    settings.SIGNED_URL_TTL_SECONDS = 1
    assert storage.resolve_access_token(token)
