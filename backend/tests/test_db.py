from __future__ import annotations

from app import db, pg


def test_init_db_is_idempotent_and_keeps_rows(database: pg.Database) -> None:
    db.init_db(database)
    conn = db.connect(database)
    document = db.create_document(conn, "LIS25 Network Requirements")
    version = db.insert_version(conn, document_id=document["id"], version=1, sha256="x", uploaded_by="t")
    db.log_download(conn, version_id=version["id"], user="alice", format="pdf")
    conn.close()

    db.init_db(database)  # every start-up runs it again

    conn = db.connect(database)
    rows = [dict(r) for r in conn.execute('SELECT "user", format FROM download_log')]
    conn.close()
    assert rows == [{"user": "alice", "format": "pdf"}]


def test_nested_transaction_failure_keeps_the_outer_one(conn: pg.Connection, document) -> None:
    """An inner ``with conn:`` is a savepoint: a duplicate caught inside it doesn't abort the outer work."""
    db.insert_version(conn, document_id=document["id"], version=1, sha256="x", uploaded_by="t")
    with conn:
        conn.execute("UPDATE documents SET archived_by = ? WHERE id = ?", ("kept", document["id"]))
        try:
            db.insert_version(conn, document_id=document["id"], version=1, sha256="y", uploaded_by="t")
        except pg.IntegrityError:
            pass
    assert db.get_document(conn, document["id"])["archived_by"] == "kept"


def test_rows_work_by_name_index_and_dict(conn: pg.Connection) -> None:
    row = conn.execute("SELECT 1 AS a, 'x' AS b").fetchone()
    assert (row["a"], row[1], dict(row)) == (1, "x", {"a": 1, "b": "x"})


def test_question_marks_inside_quotes_are_not_placeholders(conn: pg.Connection) -> None:
    row = conn.execute("SELECT '?' AS q, ? AS p, '100%' AS pct", ("v",)).fetchone()
    assert (row["q"], row["p"], row["pct"]) == ("?", "v", "100%")


def test_database_from_url() -> None:
    d = pg.Database.from_url("postgresql://app:s%40cret@db.example:5433/hub?sslmode=require")
    assert (d.host, d.port, d.dbname, d.user, d.password, d.sslmode) == (
        "db.example", 5433, "hub", "app", "s@cret", "require")
