"""HNSW indexes, built concurrently and only after the tables exist.

`atomic = False` is required: `CREATE INDEX CONCURRENTLY` cannot run inside a transaction.
`m = 16` and `ef_construction = 64` are starting values recorded as such; recall at the
operating point is measured by `search.build_ann_index` and stored on `search_annindexbuild`.
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("search", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_search_clipembedding_hnsw "
                "ON search_clipembedding USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64);"
            ),
            reverse_sql="DROP INDEX IF EXISTS ix_search_clipembedding_hnsw;",
        ),
        migrations.RunSQL(
            sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_search_dinov2embedding_hnsw "
                "ON search_dinov2embedding USING hnsw (embedding vector_cosine_ops) "
                "WITH (m = 16, ef_construction = 64);"
            ),
            reverse_sql="DROP INDEX IF EXISTS ix_search_dinov2embedding_hnsw;",
        ),
    ]
