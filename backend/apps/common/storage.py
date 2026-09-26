"""The only module that opens, writes, deletes or addresses a stored byte.

Keys are derived from an application-generated `public_id`, never from a client filename.
Writes never overwrite: a collision raises instead of renaming. `exists` is a fifth
operation so retrieval can discard a missing file without opening it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Final
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import File
from django.core.files.storage import FileSystemStorage
from django.core.signing import BadSignature, dumps, loads
from django.urls import reverse

#: Signatures issued here are valid for nothing else, even though they share `SECRET_KEY`.
_SIGNING_SALT: Final = "apps.common.storage.access"

#: `varchar(512)` in every table that stores one.
MAX_KEY_LENGTH: Final = 512

#: Lower case, digits, and the three separators a fanned-out path needs. No backslash, no
#: colon, no drive letter, no leading separator, and `..` is rejected below.
_KEY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9/._-]*$")

DERIVED_KINDS: Final = frozenset({"thumbnail", "crop", "frame", "conversion", "equalised"})


class StorageError(Exception):
    """Base class, so a caller may catch everything this module raises without importing four."""


class InvalidStorageKeyError(StorageError):
    """The key is not one this module could have produced."""


class StorageKeyCollisionError(StorageError):
    """A write was attempted against a key that already holds bytes."""


class InvalidAccessTokenError(StorageError):
    """The token was not signed here, has been altered, or has expired."""


class InvalidLocalFolderError(StorageError):
    """A local ingest root is missing, is not a directory, or is refused."""


LOCAL_IMAGE_SUFFIXES: Final = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
)


class _NoOverwriteStorage(FileSystemStorage):
    """`FileSystemStorage` renames on collision. Here that is a defect, not a convenience.

 The collision test has to be made here rather than left to the base class: `Storage.save`
 calls `get_available_name` on every write and not only when the name is taken, and the
 existence check lives inside the default implementation this one replaces.

 `FileSystemStorage._save` calls it again if the file appears between that check and the
 `O_EXCL` create. That second call is the race, and refusing it is the correct outcome: two
 writers racing for one storage key means two rows claiming the same bytes.
 """

    def get_available_name(self, name: str, max_length: int | None = None) -> str:
        if self.exists(name):
            raise StorageKeyCollisionError(
                f"{name} already holds bytes. A storage key is written exactly once."
            )
        return name


_backend: _NoOverwriteStorage | None = None


def _storage() -> _NoOverwriteStorage:
    """Constructed on first use, not at import, so that settings may load without a root set.

 The root arrives from the environment and appears nowhere in source, which is the third of
 the portability constraints at
 """
    global _backend
    if _backend is None:
        root = getattr(settings, "STORAGE_ROOT", "")
        if not root:
            raise ImproperlyConfigured(
                "STORAGE_ROOT is empty. Set SHOERAG_STORAGE_ROOT; this module refuses to "
                "guess a location for evidential files."
            )
        _backend = _NoOverwriteStorage(location=Path(root))
    return _backend


def reset_backend() -> None:
    """Discards the cached backend. For tests that move `STORAGE_ROOT` between cases."""
    global _backend
    _backend = None


# --------------------------------------------------------------------------------------
# Key construction. The only legitimate source of a storage key.
# --------------------------------------------------------------------------------------


def _fan(public_id: UUID) -> str:
    """Two levels of two hex characters, so no directory accumulates every file in the system."""
    digits = public_id.hex
    return f"{digits[0:2]}/{digits[2:4]}/{digits}"


def storage_key_for_content(public_id: UUID) -> str:
    """Original evidence. One row per distinct byte sequence, so one key per digest."""
    return f"evidence/{_fan(public_id)}"


def storage_key_for_artifact(public_id: UUID, kind: str) -> str:
    """A derived artefact. `kind` is validated because it becomes part of the path."""
    if kind not in DERIVED_KINDS:
        raise InvalidStorageKeyError(f"{kind!r} is not a derived-artefact kind")
    return f"derived/{kind}/{_fan(public_id)}"


def storage_key_for_report(public_id: UUID) -> str:
    """An exported report. Reports expire and are removed; their rows do not."""
    return f"reports/{_fan(public_id)}.pdf"


def validate_key(key: str) -> str:
    """Raises unless the key is one `storage_key_for_*` could have produced.

 Called by every entry point. A key that reaches this module from a row is still checked,
 because a row is only as trustworthy as whatever last wrote to it.
 """
    if not key or len(key) > MAX_KEY_LENGTH:
        raise InvalidStorageKeyError("A storage key is between 1 and 512 characters")
    if not _KEY_PATTERN.match(key):
        raise InvalidStorageKeyError(f"{key!r} contains a character a storage key may not contain")
    if ".." in key or key.endswith("/") or "//" in key:
        raise InvalidStorageKeyError(f"{key!r} is not a normalised relative path")
    return key


# --------------------------------------------------------------------------------------
# The four operations.
# --------------------------------------------------------------------------------------


def store(key: str, content: File | IO[bytes]) -> str:
    """Writes `content` at `key` and returns the key actually written, which always equals
 `key`: the storage refuses to rename, so a differing return is impossible rather than
 unlikely."""
    validate_key(key)
    written = _storage().save(key, content if isinstance(content, File) else File(content))
    if written != key:
        # Unreachable while `_NoOverwriteStorage` raises. Kept because the invariant that the
        # recorded key equals the written key is what the unique indexes depend on.
        raise StorageKeyCollisionError(f"storage wrote {written!r} for requested key {key!r}")
    return written


def open_stored(key: str, mode: str = "rb") -> File:
    """Opens the bytes at `key`. Named to avoid shadowing the builtin at every call site."""
    validate_key(key)
    return _storage().open(key, mode)


def exists(key: str) -> bool:
    """Whether bytes are present. Retrieval's absent-file filter is the caller."""
    validate_key(key)
    return _storage().exists(key)


def delete(key: str) -> None:
    """Removes the bytes. Deleting what is already absent is not an error, because the callers
 are expiry sweeps for which absence is the desired end state."""
    validate_key(key)
    _storage().delete(key)


def size(key: str) -> int:
    """Byte length as stored, for the digest and volume records that quote it."""
    validate_key(key)
    return _storage().size(key)


def resolve_local_folder(raw: str) -> Path:
    """Absolute directory used for local folder ingest. Off unless LOCAL_FOLDER_INGEST is set."""
    if not getattr(settings, "LOCAL_FOLDER_INGEST", False):
        raise InvalidLocalFolderError("Local folder ingest is not enabled")
    if not raw or not str(raw).strip():
        raise InvalidLocalFolderError("A folder path is required")
    try:
        resolved = Path(raw).expanduser().resolve(strict=True)
    except OSError as exc:
        raise InvalidLocalFolderError("That folder path does not exist") from exc
    if not resolved.is_dir():
        raise InvalidLocalFolderError("That folder path does not exist")
    return resolved


def iter_local_images(root: Path) -> Iterator[Path]:
    """Every image under `root`, including subfolders. Does not follow directory links."""
    resolved = resolve_local_folder(str(root))
    for dirpath, dirnames, filenames in os.walk(resolved, followlinks=False):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() in LOCAL_IMAGE_SUFFIXES:
                yield Path(dirpath) / name


def read_local_bytes(path: Path) -> bytes:
    """Reads one file discovered by `iter_local_images`."""
    return path.read_bytes()


# --------------------------------------------------------------------------------------
# Signed access. Locally an HMAC token; with an object store, that store's own signed URL.
# The interface does not change, so no caller learns which it is.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SignedAccess:
    """What a caller needs in order to answer `POST /evidence/{id}/access-url/`."""

    url: str
    expires_at: datetime


def default_ttl() -> timedelta:
    """Short, measured in minutes, and stated in configuration rather than here."""
    return timedelta(seconds=int(getattr(settings, "SIGNED_URL_TTL_SECONDS", 600)))


def signed_url(key: str, *, ttl: timedelta | None = None) -> SignedAccess:
    """Issues a time-limited grant over `key`.

 The expiry is inside the signed payload rather than applied when the token is presented.
 That is deliberate: the lifetime is fixed at the moment authorisation was evaluated, so
 neither the holder nor a later change of configuration can extend a grant already issued.
 """
    validate_key(key)
    expires_at = datetime.now(tz=UTC) + (ttl or default_ttl())
    # `dumps` rather than `Signer` so the token is opaque and URL-safe. Signing the key as
    # plain text would put its slashes into the path, which would both require a `path`
    # converter on the route and print the storage layout in every server log.
    token = dumps({"k": key, "e": int(expires_at.timestamp())}, salt=_SIGNING_SALT)
    return SignedAccess(
        url=reverse("stored-object", kwargs={"token": token}),
        expires_at=expires_at,
    )


def resolve_access_token(token: str) -> str:
    """Returns the storage key a token grants, or raises.

 Failure is one exception with one message whichever way it failed. A response that
 distinguished an altered token from an expired one would tell a holder which half to
 attack.
 """
    try:
        payload = loads(token, salt=_SIGNING_SALT)
        key = payload["k"]
        expires_at = int(payload["e"])
    except (BadSignature, TypeError, KeyError, ValueError) as exc:
        raise InvalidAccessTokenError("The access token is not valid") from exc

    if datetime.now(tz=UTC).timestamp() >= expires_at:
        raise InvalidAccessTokenError("The access token is not valid")

    try:
        return validate_key(key)
    except InvalidStorageKeyError as exc:
        raise InvalidAccessTokenError("The access token is not valid") from exc
