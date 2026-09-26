"""The two paginators.

`User` is the queryset under test because it is the only table that exists yet, and these
properties are about the paginators rather than about any particular collection.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest
from django.contrib.auth import get_user_model
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.common.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    GrowingCollectionPagination,
    StandardPagination,
)

User = get_user_model()

pytestmark = pytest.mark.unit


def request_for(query: str = "") -> Request:
    """A DRF request, not a bare WSGI one: the paginators read `query_params`, which only the
 DRF wrapper provides."""
    return Request(APIRequestFactory().get(f"/api/v1/things/{query}"))


def test_defaults_are_the_documented_pair() -> None:
    assert DEFAULT_PAGE_SIZE == 25
    assert MAX_PAGE_SIZE == 100


def test_page_size_above_the_maximum_is_clamped_not_rejected() -> None:
    """OWASP API4. Clamping keeps a legitimate client working while bounding the
 response; rejecting would break it, and honouring would make the limit decorative."""
    paginator = StandardPagination()
    assert paginator.get_page_size(request_for("?page_size=5000")) == MAX_PAGE_SIZE


def test_page_size_below_the_maximum_is_honoured() -> None:
    assert StandardPagination().get_page_size(request_for("?page_size=10")) == 10


def test_absent_page_size_falls_back_to_the_default() -> None:
    assert StandardPagination().get_page_size(request_for()) == DEFAULT_PAGE_SIZE


def test_cursor_class_shares_the_same_limits() -> None:
    """A client should not have to learn two sets of limits because it moved to another
 collection."""
    assert (
        GrowingCollectionPagination().get_page_size(request_for("?page_size=5000")) == MAX_PAGE_SIZE
    )


def test_cursor_ordering_is_a_total_order() -> None:
    """. Ordering on a timestamp alone is not total when two rows share a value, and the
 failure - a row on two consecutive pages, or on neither - is intermittent and very hard to
 diagnose from a user's report. The last term must therefore be unique."""
    ordering = GrowingCollectionPagination.ordering
    assert isinstance(ordering, tuple)
    assert len(ordering) >= 2
    assert ordering[-1].lstrip("-") == "id"


@pytest.mark.django_db
def test_page_number_envelope_carries_a_count() -> None:
    for index in range(3):
        User.objects.create_user(username=f"counted-{index}", password="correct-horse-battery")
    paginator = StandardPagination()
    request = request_for()
    page = paginator.paginate_queryset(User.objects.order_by("id"), request)
    body = paginator.get_paginated_response([{"id": user.pk} for user in page]).data
    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 3


@pytest.mark.django_db
def test_cursor_envelope_omits_the_count() -> None:
    """An exact total over tens of millions of rows is a scan, and paying for one on every page
 request in order to display a total is not a trade this system makes."""
    User.objects.create_user(username="cursored", password="correct-horse-battery")
    paginator = GrowingCollectionPagination()
    paginator.ordering = ("-date_joined", "-id")
    request = request_for()
    page = paginator.paginate_queryset(User.objects.all(), request)
    body = paginator.get_paginated_response([{"id": user.pk} for user in page]).data
    assert set(body) == {"next", "previous", "results"}


@pytest.mark.django_db
def test_cursor_pages_return_each_row_exactly_once() -> None:
    """The assertion that matters for cursor pagination. Rows are created in one transaction so
 several share a timestamp, which is precisely the tie the compound ordering exists for."""
    for index in range(7):
        User.objects.create_user(username=f"walked-{index:02d}", password="correct-horse-battery")

    paginator = GrowingCollectionPagination()
    paginator.ordering = ("-date_joined", "-id")
    seen: list[int] = []
    query = "?page_size=2"

    while True:
        page = paginator.paginate_queryset(User.objects.all(), request_for(query))
        seen.extend(user.pk for user in page)
        link = paginator.get_next_link()
        if link is None:
            break
        # Follow the paginator's own link rather than reconstructing one, so the test walks the
        # pages a client would walk.
        query = f"?{urlparse(link).query}"

    assert len(seen) == len(set(seen)) == 7
