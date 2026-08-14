"""The tsv column is intentionally absent from the SQLAlchemy metadata.

SQLite `create_all` in tests cannot take a `tsvector`, and nothing reads the
column through the ORM -- retrieval uses raw text() SQL. The cost of that
choice is that alembic autogenerate sees a column in the database and not in
the metadata, and proposes dropping it. This test pins the guard that stops
it, because the failure is silent: a future `make revision` would emit
`op.drop_column('paper_chunk_embeddings', 'tsv')` and take the GIN index with
it, and retrieval would degrade to dense-only with no error anywhere.
"""

from app.db.autogenerate import include_object


class _Obj:
    def __init__(self, name: str):
        self.name = name


def test_the_tsv_column_is_excluded_from_autogenerate():
    assert (
        include_object(
            _Obj("tsv"), "tsv", "column", True, None, parent_table="paper_chunk_embeddings"
        )
        is False
    )


def test_the_tsv_index_is_excluded_from_autogenerate():
    assert (
        include_object(
            _Obj("ix_paper_chunk_embeddings_tsv"),
            "ix_paper_chunk_embeddings_tsv",
            "index",
            True,
            None,
        )
        is False
    )


def test_other_columns_are_still_compared():
    assert (
        include_object(
            _Obj("text"), "text", "column", True, None, parent_table="paper_chunk_embeddings"
        )
        is True
    )


def test_a_tsv_column_on_another_table_is_still_compared():
    """The exclusion is scoped to one table's one column, not to the name.
    A future `tsv` elsewhere must not inherit this hole."""
    assert (
        include_object(_Obj("tsv"), "tsv", "column", True, None, parent_table="some_other_table")
        is True
    )
