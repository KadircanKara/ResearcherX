"""Alembic autogenerate object filter — kept as a real package module (not
inside `alembic/env.py`) because `alembic/` is a migration *directory*, not
an importable package: `import alembic` resolves to the installed library,
so `from alembic.env import include_object` cannot work. `alembic/env.py`
imports this module and passes it to `context.configure(include_object=...)`.
"""

_EXCLUDED_COLUMNS = {("paper_chunk_embeddings", "tsv")}
_EXCLUDED_INDEXES = {"ix_paper_chunk_embeddings_tsv"}


def include_object(obj, name, type_, reflected, compare_to, **kw) -> bool:
    """Keep autogenerate away from objects the metadata deliberately omits.

    `paper_chunk_embeddings.tsv` is a generated tsvector column with a GIN
    index. It is NOT declared on the model: SQLite `create_all` in tests
    cannot take a `tsvector`, and retrieval reads it only through raw text()
    SQL. Without this hook autogenerate compares a database that has the
    column against metadata that does not and emits a `drop_column` -- which
    would take the GIN index with it and silently degrade retrieval to
    dense-only, with no error at any layer.
    """
    if type_ == "column":
        parent = kw.get("parent_table")
        if parent is None:
            # The installed alembic (1.18.5) does not actually pass a
            # `parent_table` keyword for the drop-column case that matters
            # here -- it calls run_object_filters(conn_table.c[cname], cname,
            # "column", True, None) with five positional args only. The
            # column object itself is bound to its table, so fall back to
            # obj.table.name. The `parent_table` kwarg is kept as the
            # primary lookup for forward/backward compatibility with alembic
            # versions that do pass it (and it's what the pinned tests use).
            parent = getattr(obj, "table", None)
        parent_name = getattr(parent, "name", parent)
        if (parent_name, name) in _EXCLUDED_COLUMNS:
            return False
    if type_ == "index" and name in _EXCLUDED_INDEXES:
        return False
    return True
