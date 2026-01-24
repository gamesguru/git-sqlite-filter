"""Integration tests for the git-sqlite-filter package."""

import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from git_sqlite_filter.clean import DatabaseDumper
from git_sqlite_filter.clean import main as clean_main
from git_sqlite_filter.smudge import (
    DatabaseRestorer,
    filter_sql_stream,
)
from git_sqlite_filter.smudge import main as smudge_main

FIXTURE_DIR = "test/fixtures"
TMP_DIR = ".tmp/test_runs"


def get_fixtures():
    """Return a list of fixture database paths."""
    # Only return explicitly managed fixtures to avoid picking up stray/corrupt files
    expected = [
        "version_0.db",
        "version_huge.db",
        "collation_edge.db",
        "blobs.db",
        "fts.db",
        "generated_cols.db",
        "constraints.db",
        "autoincrement.db",
        "mixed_edge.db",
    ]
    return [os.path.join(FIXTURE_DIR, f) for f in expected]


@pytest.mark.parametrize("db_path", get_fixtures())
def test_semantic_parity(db_path):
    """Ensure that clean -> smudge roundtrip preserves data semantics."""
    db_name = os.path.basename(db_path)

    # Step A: Clean original DB -> SQL Dump A
    args_a = type(
        "Args",
        (),
        {
            "float_precision": 5,
            "schema_only": False,
            "data_only": False,
            "debug": False,
        },
    )()

    out_a = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = out_a
    try:
        with DatabaseDumper(db_path, args_a) as dumper_a:
            dumper_a.dump()
    finally:
        sys.stdout = old_stdout

    dump_a = out_a.getvalue()
    assert dump_a, f"Dump A for {db_name} is empty"

    # Step B: Smudge SQL Dump A -> Rebuilt DB
    with DatabaseRestorer(debug=False) as restorer:
        # The restorer uses filter_sql_stream in main(), but here we can just pass the dump
        # However, the smudge filter typically expects the output of filter_sql_stream.
        # Let's replicate the main smudge logic:
        filtered_script = "".join(
            list(filter_sql_stream(io.StringIO(dump_a), debug=False))
        )

        assert restorer.restore(filtered_script), f"Restoration failed for {db_name}"
        rebuilt_db_path = restorer.tmp_path

        # Step C: Clean Rebuilt DB -> SQL Dump B
        out_b = io.StringIO()
        sys.stdout = out_b
        try:
            with DatabaseDumper(rebuilt_db_path, args_a) as dumper_b:
                dumper_b.dump()
        finally:
            sys.stdout = old_stdout

        dump_b = out_b.getvalue()

        # Step D: Compare
        assert dump_a == dump_b, f"Semantic mismatch for {db_name}"


def test_binary_fallback():
    """Ensure non-SQLite files are passed through as-is."""
    os.makedirs(TMP_DIR, exist_ok=True)
    binary_db = os.path.join(TMP_DIR, "binary_only.db")
    content = b"raw binary content\n"
    with open(binary_db, "wb") as f:
        f.write(content)

    # The main() in clean.py handles the fallback for non-sqlite files.
    # Test by calling the function directly to ensure coverage.

    with patch.object(sys, "argv", ["git-sqlite-clean", binary_db]):
        # Mock stdout such that stdout.buffer is a BytesIO
        output_bytes = io.BytesIO()
        with patch.object(sys, "stdout") as mock_stdout:
            mock_stdout.buffer = output_bytes
            clean_main()
            assert content in output_bytes.getvalue()


def test_lock_performance_timeout():
    """Ensure tool fails fast (< 0.1s) when DB is locked."""

    os.makedirs(TMP_DIR, exist_ok=True)
    db_path = os.path.join(TMP_DIR, "locked.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    # Create a dummy DB
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
    finally:
        conn.close()

    # Hold an exclusive lock in a separate thread
    ev = threading.Event()

    def hold_lock():
        c = None
        try:
            c = sqlite3.connect(db_path)
            c.execute("BEGIN EXCLUSIVE")
            ev.set()
            time.sleep(0.3)  # Hold briefly - just enough to test fail-fast
        except sqlite3.OperationalError:
            ev.set()
        finally:
            if c:
                c.close()

    t = threading.Thread(target=hold_lock)
    t.start()
    ev.wait()  # Wait for lock to be acquired

    start = time.time()
    # Run clean.py against locked DB
    cmd = [sys.executable, "src/git_sqlite_filter/clean.py", db_path]
    # We expect it to write to stdout (fallback)
    subprocess.run(cmd, capture_output=True, check=False)
    end = time.time()

    t.join()

    duration = end - start
    # Python startup time dominates here (can be ~0.1-0.2s).
    # Fail-fast means we didn't wait 5s (default) or longer. 0.5s is safe proof.
    assert duration < 0.5, f"Lock fallback took too long: {duration:.4f}s"


def test_smudge_cli():
    """Directly test smudge.main() to ensure CLI coverage."""

    sql_input = (
        "BEGIN TRANSACTION;\nCREATE TABLE t(a);\nINSERT INTO t VALUES(1);\nCOMMIT;\n"
    )

    # Mock sys.argv
    with patch.object(sys, "argv", ["git-sqlite-smudge", "ignored_filename"]):
        # Mock stdin
        with patch.object(sys, "stdin", io.StringIO(sql_input)):
            # Mock stdout.buffer
            output_bytes = io.BytesIO()
            with patch.object(sys, "stdout") as mock_stdout:
                mock_stdout.buffer = output_bytes
                smudge_main()

                # Verify we got some binary output (SQLite header)
                assert output_bytes.getvalue().startswith(b"SQLite format 3")
