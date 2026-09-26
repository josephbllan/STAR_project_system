"""The endpoint that redeems a signed access token.

It exists so that evidential bytes are never served by the API itself: the API
issues a grant by `POST` after authorisation has been evaluated, and this view redeems it. With
an object store in front, `storage.signed_url` returns the store's URL and this view is never
reached; the caller's code is identical either way.

Authorisation is not evaluated here, and that is the design rather than an omission. The
token is the grant. It is unguessable without `SECRET_KEY`, it names exactly one storage key,
and it expires. What this view must therefore never do is trust anything else in the request.
"""

from __future__ import annotations

from django.http import FileResponse, Http404, HttpRequest
from django.views.decorators.http import require_safe

from apps.common import storage


@require_safe
def stored_object(_request: HttpRequest, token: str) -> FileResponse:
    """Streams the bytes a valid token grants.

 Every failure is a 404. An invalid token, an expired token and a token naming a key whose
 bytes have gone are indistinguishable from outside, because saying which would confirm
 that a given key exists.
 """
    try:
        key = storage.resolve_access_token(token)
    except storage.InvalidAccessTokenError as exc:
        raise Http404 from exc

    if not storage.exists(key):
        raise Http404

    response = FileResponse(storage.open_stored(key), as_attachment=False)
    # Stated rather than inherited. `FileResponse` derives a filename from the path it was
    # given, which here would publish the last segment of the storage key. There is no filename
    # worth offering: the client-supplied one lives on the registration row for provenance and
    # is never used to build a path, an identifier or a header.
    response.headers["Content-Disposition"] = "inline"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response
