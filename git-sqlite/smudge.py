#!/usr/bin/env python3
import argparse
import os
import re
import signal
import subprocess
import sys
import tempfile
import sqlite3

# Handle broken pipes (e.g. | head) without stack trace
TOOL = "[git-sqlite-smudge]"


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def filter_sql_stream(stream):
    """Generator that filters transaction control and internal tables from SQL stream."""
    tx_patterns = ["BEGIN TRANSACTION", "COMMIT", "ROLLBACK"]
    
    yield "BEGIN TRANSACTION;\n"
    
    for line in stream:
        # Skip creating sqlite_sequence (it's internal and auto-created)
        if "CREATE TABLE" in line and "sqlite_sequence" in line:
            continue

        # Skip sqlite_master/stat internal metadata
        if "sqlite_master" in line or "sqlite_stat" in line:
            if "CREATE VIEW" not in line:
                continue

        # Filter transaction commands to prevent nested transactions
        line_upper = line.upper().strip()
        is_tx = False
        for p in tx_patterns:
            if line_upper.startswith(p) or line_upper.endswith(p + ";"):
                is_tx = True
                break
        if is_tx:
            continue

        yield line
        
    yield "COMMIT;\n"


def collation_func(s1, s2):
    # Dummy collation: sort lexicographically
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


def main():
    parser = argparse.ArgumentParser(description="Git smudge filter for SQLite")
    parser.add_argument("db_file", nargs="?", help="Ignored but passed by Git")
    parser.add_argument("--schema", help="Path to a base schema file to apply before data")
    
    args = parser.parse_args()

    # Read all SQL into memory for the collation-retry loop
    sql_lines = []
    if args.schema and os.path.exists(args.schema):
        log(f"Loading schema from {args.schema}")
        with open(args.schema, "r") as f:
            sql_lines.extend(f.readlines())
    
    sql_lines.extend(list(filter_sql_stream(sys.stdin)))
    script = "".join(sql_lines)

    # create a temp db
    fd, tmp_db_path = tempfile.mkstemp(prefix="sqlite_smudge_", suffix=".sqlite")
    os.close(fd)

    conn = None
    try:
        conn = sqlite3.connect(tmp_db_path)
        registered_collations = set()
        max_retries = 100
        
        for _ in range(max_retries):
            try:
                # We use executescript for the filtered script
                # Note: filter_sql_stream already adds BEGIN/COMMIT
                conn.executescript(script)
                break
            except sqlite3.OperationalError as e:
                msg = str(e)
                match = re.search(r"no such collation sequence: (\S+)", msg)
                if match:
                    col_name = match.group(1).strip("'\"")
                    if col_name not in registered_collations:
                        log(f"registering missing collation: {col_name}")
                        conn.create_collation(col_name, collation_func)
                        registered_collations.add(col_name)
                        
                        # Re-initialize DB to retry
                        conn.close()
                        os.remove(tmp_db_path)
                        fd, tmp_db_path = tempfile.mkstemp(prefix="sqlite_smudge_", suffix=".sqlite")
                        os.close(fd)
                        conn = sqlite3.connect(tmp_db_path)
                        for existing_col in registered_collations:
                            conn.create_collation(existing_col, collation_func)
                        continue
                log(f"error: sqlite3 smudge failed: {e}")
                sys.exit(1)

        conn.close()
        conn = None

        # Stream resulting binary DB to stdout
        with open(tmp_db_path, "rb") as f:
            sys.stdout.buffer.write(f.read())

    finally:
        if conn:
            conn.close()
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)


if __name__ == "__main__":
    main()
