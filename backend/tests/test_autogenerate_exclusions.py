"""The tsv column is intentionally absent from the SQLAlchemy metadata.

SQLite `create_all` in tests cannot take a `tsvector`, and nothing reads the
column through the ORM -- retrieval uses raw text() SQL. The cost of that
choice is that alembic autogenerate sees a column in the database and not in
the metadata, and proposes dropping it. This test pins the guard that stops
it, because the failure is silent: a future `make revision` would emit
`op.drop_column('paper_chunk_embeddings', 'tsv')` and take the GIN index with
it, and retrieval would degrade to dense-only with no error anywhere.
"""

from sqlalchemy import Column, MetaData, Table, Text

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


def test_fallback_branch_scopes_by_column_table_when_no_parent_table_kwarg():
    """alembic 1.18.5 never actually passes a `parent_table` keyword.

    Traced live: `run_object_filters` in `alembic/autogenerate/api.py` calls
    the object filter as `fn(object_, name, type_, reflected, compare_to)` --
    five positional arguments, no kwargs at all -- and for the drop-column
    case that matters here (`_compare_columns` in
    `alembic/autogenerate/compare/tables.py`), `object_` is the real
    `Column`, already bound to its table via `conn_table.c[cname]`. So the
    branch production actually takes is `obj.table.name`, not the
    `parent_table` kwarg -- and the four tests above, which all pass
    `parent_table=` explicitly, never touch it. This test uses real
    `sqlalchemy.Column`/`Table` objects (not a stub) so it can't pass by
    accident the way a stub without `.table` did.
    """
    metadata = MetaData()
    chunks_table = Table("paper_chunk_embeddings", metadata, Column("tsv", Text))
    other_table = Table("some_other_table", metadata, Column("tsv", Text))

    assert (
        include_object(chunks_table.c["tsv"], "tsv", "column", True, None) is False
    )  # excluded: same table + column as the guard
    assert (
        include_object(other_table.c["tsv"], "tsv", "column", True, None) is True
    )  # still compared: same column name, different table
