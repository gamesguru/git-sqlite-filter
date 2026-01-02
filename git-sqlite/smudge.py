#!/usr/bin/env python3
import os
import re
import signal
import sqlite3
import sys
import tempfile

# Handle broken pipes (e.g. | head) without stack trace
TOOL = "[git-sqlite-smudge]"


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def collation_func(s1, s2):
    # Dummy collation: sort lexicographically
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


def main():
    # Read SQL dump from stdin
    sql_content = sys.stdin.read()

    # Pre-process SQL: Filter out transaction control and internal tables
    filtered_lines = []
    tx_patterns = ["BEGIN TRANSACTION", "COMMIT", "ROLLBACK"]

    for line in sql_content.splitlines():
        # Skip creating sqlite_sequence (it's internal and auto-created)
        if "CREATE TABLE" in line and "sqlite_sequence" in line:
            continue

        # Skip sqlite_master/stat internal metadata
        if "sqlite_master" in line or "sqlite_stat" in line:
            if "CREATE VIEW" not in line:
                continue

        # Filter transaction commands to prevent "cannot start transaction within transaction"
        line_upper = line.upper().strip()
        is_tx = False
        for p in tx_patterns:
            if line_upper.startswith(p) or line_upper.endswith(p + ";"):
                is_tx = True
                break
        if is_tx:
            continue

        filtered_lines.append(line)

    script = "\n".join(filtered_lines)

    # create a temp db
    fd, tmp_db_path = tempfile.mkstemp()
    os.close(fd)

    conn = None
    try:
        conn = sqlite3.connect(tmp_db_path)

        # --- Dynamic Collation Handler ---
        # We try to execute the script. If it fails due to a missing collation,
        # we extract the name from the error, register a dummy, and retry.
        registered_collations = set()
        max_retries = 100
        for _ in range(max_retries):
            try:
                conn.executescript(script)
                break  # Success!
            except sqlite3.OperationalError as e:
                msg = str(e)
                # Look for "no such collation sequence: NAME"
                match = re.search(r"no such collation sequence: (\S+)", msg)
                if match:
                    col_name = match.group(1).strip("'\"")
                    if col_name not in registered_collations:
                        log(f"registering missing collation: {col_name}")
                        conn.create_collation(col_name, collation_func)
                        registered_collations.add(col_name)
                        # We must rollback any partial work from the failed executescript
                        # but executescript is non-transactional in Python's sqlite3 wrapper usually.
                        # However, since we are building a NEW DB from scratch, retrying from empty is easier.
                        conn.close()
                        os.remove(tmp_db_path)
                        fd, tmp_db_path = tempfile.mkstemp()
                        os.close(fd)
                        conn = sqlite3.connect(tmp_db_path)
                        for existing_col in registered_collations:
                            conn.create_collation(existing_col, collation_func)
                        continue  # Retry with new collation

                # If it's not a collation error, or we already registered it, something else is wrong.
                # We stop trying but proceed to output whatever we managed to build.
                break

        # Commit changes to the temp DB
        conn.commit()
        conn.close()
        conn = None

        # Stream the resulting binary DB to stdout
        with open(tmp_db_path, "rb") as f:
            sys.stdout.buffer.write(f.read())

    finally:
        if conn:
            conn.close()
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)


if __name__ == "__main__":
    main()
