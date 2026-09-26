"""Query-count budgets for the four paths the schema exists to serve."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.scenarios import completed_run

pytestmark = [pytest.mark.django_db]


def test_result_page_query_count_is_bounded() -> None:
    bundle = completed_run
    query = bundle.query
    with CaptureQueriesContext(connection) as captured:
        list(query.results.select_related("evidence_file__content").order_by("rank")[:25])
    assert len(captured) <= 5
