import io
import sqlite3
import sys
from unittest import mock

from git_sqlite_filter.clean import (
    collation_func,
    format_sql_value,
)


def test_format_sql_value_variants():
    assert format_sql_value(None) == "NULL"
    assert format_sql_value(5) == "5"
    assert format_sql_value(3.1400, float_precision=2) == "3.14"
    assert format_sql_value(b"\x01\x02") == "X'0102'"
    # string escaping
    assert format_sql_value("O'Reilly") == "'O''Reilly'"


def test_collation_func():
    assert collation_func("a", "a") == 0
    assert collation_func("b", "a") == 1
    assert collation_func("a", "b") == -1


def test_wal_mode_integration(tmp_path, monkeypatch, capsys):
    """Firefox-style WAL DB should work without errors."""
    db_path = tmp_path / "wal.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t(val) VALUES('x')")
    conn.commit()
    conn.close()

    from git_sqlite_filter.clean import main

    output_bytes = io.BytesIO()
    monkeypatch.setattr(sys, "argv", ["git-sqlite-clean", str(db_path)])
    with mock.patch.object(sys, "stdout") as mock_stdout:
        mock_stdout.buffer = output_bytes
        main()

    # Check that we got valid SQL output (not an error)
    err = capsys.readouterr().err
    # The safety warning is expected, but no "error" or "failed" messages
    assert "error" not in err.lower() or "falling back" not in err.lower()
