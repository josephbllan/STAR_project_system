"""Redemption of a signed access grant.

The properties under test are the ones that make it acceptable for this endpoint to require no
session: the token is the grant, it names exactly one key, it expires, and every failure looks
identical from outside.
"""

from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from django.urls import reverse

from apps.common import storage

pytestmark = pytest.mark.integration


def stored(tmp_storage) -> str:
    return storage.store(storage.storage_key_for_content(uuid4()), BytesIO(b"evidential bytes"))


def token_for(key: str, **kwargs) -> str:
    return storage.signed_url(key, **kwargs).url.rsplit("/", 1)[-1]


def test_valid_token_streams_the_bytes(client, tmp_storage) -> None:
    response = client.get(
        reverse("stored-object", kwargs={"token": token_for(stored(tmp_storage))})
    )
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"evidential bytes"


def test_expired_token_is_not_found(client, tmp_storage) -> None:
    token = token_for(stored(tmp_storage), ttl=timedelta(seconds=-1))
    assert client.get(reverse("stored-object", kwargs={"token": token})).status_code == 404


def test_altered_token_is_not_found(client, tmp_storage) -> None:
    token = token_for(stored(tmp_storage))
    altered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert client.get(reverse("stored-object", kwargs={"token": altered})).status_code == 404


def test_absent_bytes_are_not_found(client, tmp_storage) -> None:
    """A validly signed token for a key whose file has been swept must not produce a 500."""
    token = token_for(storage.storage_key_for_content(uuid4()))
    assert client.get(reverse("stored-object", kwargs={"token": token})).status_code == 404


def test_every_failure_is_the_same_status(client, tmp_storage) -> None:
    """An invalid token, an expired one and a missing file are indistinguishable. Distinguishing
 them would confirm that a given storage key exists."""
    tokens = [
        "not-a-token",
        token_for(stored(tmp_storage), ttl=timedelta(seconds=-1)),
        token_for(storage.storage_key_for_content(uuid4())),
    ]
    statuses = {
        client.get(reverse("stored-object", kwargs={"token": t})).status_code for t in tokens
    }
    assert statuses == {404}


def test_unsafe_methods_are_refused(client, tmp_storage) -> None:
    """Issuing a credential is a state change and belongs to the `POST` that mints it; redeeming
 one reads bytes. Nothing here may be written through this route."""
    url = reverse("stored-object", kwargs={"token": token_for(stored(tmp_storage))})
    assert client.post(url).status_code == 405
    assert client.delete(url).status_code == 405


def test_response_is_not_cached_and_not_sniffed(client, tmp_storage) -> None:
    """A shared cache holding evidential bytes would outlive the grant that released them."""
    response = client.get(
        reverse("stored-object", kwargs={"token": token_for(stored(tmp_storage))})
    )
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_no_content_disposition_carries_a_filename(client, tmp_storage) -> None:
    """The original filename lives on the registration row for provenance and is never
 used to construct a path, an identifier or a header."""
    response = client.get(
        reverse("stored-object", kwargs={"token": token_for(stored(tmp_storage))})
    )
    assert "filename" not in response.headers.get("Content-Disposition", "")
