from datalab import db


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "datalab.sqlite"
    connection = db.connect(path)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "queries" in tables
    assert db.migrate(connection) == []  # nothing left to apply
    connection.close()
    assert db.migrate(db.connect(path)) == []
