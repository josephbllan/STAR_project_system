"""Pagination, chosen per collection rather than set once.

Page-number pagination for bounded collections - cases, corpora, users, reports - where jumping
to a page and knowing the total are genuinely useful and both are cheap. Cursor pagination for
`cases_result`, `audit_auditevent` and `tasks_taskrun`, which are expected to reach millions of
rows, where offset pagination both drifts and degrades: a row inserted between two requests
shifts every subsequent page, and `OFFSET 50000` makes the database produce and discard fifty
thousand rows. Neither problem is visible in development and both are certain in production.

The page-number class is also the project default, so a collection cannot be left unpaginated by
omission. Pagination is a resource-consumption control as much as an ergonomic one: the maximum
page size is enforced on the server, and a larger request is clamped rather than rejected, which
keeps a legitimate client working while bounding the response.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response

#: Both classes share these, because a client should not have to learn two sets of limits.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
PAGE_SIZE_PARAM = "page_size"


class StandardPagination(PageNumberPagination):
    """Bounded collections. Default 25, maximum 100, total count included."""

    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = PAGE_SIZE_PARAM
    max_page_size = MAX_PAGE_SIZE


class GrowingCollectionPagination(CursorPagination):
    """Collections that grow without bound.

 `ordering` must be a compound key ending in a unique column, and subclasses override it
 rather than inherit this default blindly. Ordering on a timestamp alone is not a total order
 when two rows share a value, and the failure - a row appearing on two consecutive pages, or
 on neither - is intermittent and very hard to diagnose from a user's report.

 `count` is absent from the response by design. An exact total over tens of millions of rows
 is a scan, and paying for one on every page request to display "showing 25 of 4,812" is not a
 trade this system makes. specifies the affected screens accordingly.
 """

    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = PAGE_SIZE_PARAM
    max_page_size = MAX_PAGE_SIZE
    ordering = ("-created_at", "-id")

    def get_paginated_response(self, data: Sequence[Any]) -> Response:
        """The same envelope as the page-number class, minus `count`.

 DRF's own cursor response already omits it; this override exists to state the shape in
 one place so that a reader comparing the two classes can see the single difference.
 """
        return Response(
            {
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )
