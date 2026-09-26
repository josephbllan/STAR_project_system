"""The storage module against a real filesystem.

These are the operations every other module is forbidden from performing itself, so if the
behaviour here is wrong it is wrong everywhere at once.
"""

from io import BytesIO
from uuid import uuid4

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.common import storage

pytestmark = pytest.mark.integration


def key() -> str:
    return storage.storage_key_for_content(uuid4())


def test_store_then_open_returns_the_same_bytes(tmp_storage) -> None:
    written = storage.store(key, BytesIO(b"evidential bytes"))
    with storage.open_stored(written) as handle:
        assert handle.read() == b"evidential bytes"


def test_store_returns_the_key_it_was_given(tmp_storage) -> None:
    """The recorded key must equal the written key: the unique indexes on `storage_key` exist to
 prevent overwrite, and they are worthless if the two can differ."""
    requested = key
    assert storage.store(requested, BytesIO(b"x")) == requested


def test_second_write_to_the_same_key_is_refused(tmp_storage) -> None:
    """`FileSystemStorage` renames on collision. Here that would put different bytes on disk
 from the ones the row claims, so it raises instead."""
    existing = key
    storage.store(existing, BytesIO(b"first"))
    with pytest.raises(storage.StorageKeyCollisionError):
        storage.store(existing, BytesIO(b"second"))


def test_original_bytes_survive_a_refused_overwrite(tmp_storage) -> None:
    """The point of refusing is that evidence is not modified. A raise that had already
 truncated the file would be worse than a rename."""
    existing = key
    storage.store(existing, BytesIO(b"original"))
    with pytest.raises(storage.StorageKeyCollisionError):
        storage.store(existing, BytesIO(b"replacement"))
    with storage.open_stored(existing) as handle:
        assert handle.read() == b"original"


def test_exists_reports_absence_without_opening(tmp_storage) -> None:
    """Retrieval discards results whose underlying file has gone. Expressing that as a caught
 exception around `open` would mean opening a file to learn that it is absent."""
    assert storage.exists(key) is False
    present = storage.store(key, BytesIO(b"here"))
    assert storage.exists(present) is True


def test_delete_removes_the_bytes(tmp_storage) -> None:
    doomed = storage.store(key, BytesIO(b"expiring report"))
    storage.delete(doomed)
    assert storage.exists(doomed) is False


def test_deleting_what_is_absent_is_not_an_error(tmp_storage) -> None:
    """The callers are expiry sweeps, for which absence is the desired end state. A
 sweep that failed on an already-removed file would stop part way through."""
    storage.delete(key)


def test_size_matches_what_was_written(tmp_storage) -> None:
    written = storage.store(key, BytesIO(b"0123456789"))
    assert storage.size(written) == 10


def test_bytes_are_written_beneath_the_configured_root(tmp_storage) -> None:
    """Nothing escapes the root. This is the assertion that a traversal in a key would break."""
    written = storage.store(key, BytesIO(b"x"))
    assert (tmp_storage / written).is_file()


def test_traversal_is_refused_before_any_filesystem_call(tmp_storage) -> None:
    for operation in (storage.exists(), storage.delete(), storage.open_stored):
        with pytest.raises(storage.InvalidStorageKeyError):
            operation("../escaped")


def test_module_refuses_to_guess_a_root(settings) -> None:
    """An unset root is a configuration error, not a reason to default to somewhere. Writing
 evidence to a guessed location would be worse than failing to start."""
    settings.STORAGE_ROOT = ""
    storage.reset_backend
    try:
        with pytest.raises(ImproperlyConfigured, match="SHOERAG_STORAGE_ROOT"):
            storage.exists(key)
    finally:
        storage.reset_backend
