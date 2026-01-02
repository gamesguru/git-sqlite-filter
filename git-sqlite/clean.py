#!/usr/bin/env python3
import argparse
import os
import re
import signal
import subprocess
import sys
import tempfile
import sqlite3

# Handle broken pipes gracefully
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-clean]"


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def normalize_floats(line, precision):
    """Normalize floats in INSERT statements using regex."""
    if not line.startswith("INSERT INTO"):
        return line

    def round_match(match):
        val = match.group(0)
        try:
            # Round the float to specified precision
            return format(float(val), f".{precision}f").rstrip("0").rstrip(".")
        except ValueError:
            return val

    # Match numbers that look like floats (have a decimal point)
    return re.sub(r"-?\d+\.\d+(?:[eE][-+]?\d+)?", round_match, line)


def collation_func(s1, s2):
    # Dummy collation: sort lexicographically
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


def stream_dump(db_path, args):
    """Stream logical SQL dump using Python's iterdump for collation support."""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        
        # 1. PRAGMA user_version
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        
        # 2. Setup Collation Handling
        # Since iterdump() might fail halfway if it hits a custom collation,
        # and we are streaming to stdout, we have a problem.
        # Strategy: Pre-scan sqlite_master for collation names?
        # Faster: Use a very high retry count and hope we catch them all.
        registered_collations = set()
        max_attempts = 100
        
        for attempt in range(max_attempts):
            output_buffer = [] # Buffer ONLY if we expect errors, but that's slow.
            # Realistically, we'll just try to dump. If it fails, we register and try again.
            # To avoid the "partial output" problem, we use a temporary file for the dump 
            # and then stream it to stdout only on success.
            
            with tempfile.NamedTemporaryFile(mode="w+", prefix="sqlite_clean_sql_", suffix=".sql", delete=True) as sql_tmp:
                try:
                    if not args.data_only:
                        sql_tmp.write(f"PRAGMA user_version = {user_version};\n")
                        sql_tmp.write("PRAGMA foreign_keys=OFF;\n")

                    # iterdump() handles the full SCHEMA + DATA dump
                    for line in conn.iterdump():
                        # Filtering
                        if args.data_only and not line.startswith("INSERT INTO"):
                            continue
                        if args.schema_only and line.startswith("INSERT INTO"):
                            continue
                        
                        # Normalization
                        if args.float_precision is not None:
                            line = normalize_floats(line, args.float_precision)
                        
                        sql_tmp.write(line + "\n")
                    
                    # If we reached here, success! Flush and stream to stdout.
                    sql_tmp.seek(0)
                    for line in sql_tmp:
                        sys.stdout.write(line)
                    return True
                    
                except sqlite3.OperationalError as e:
                    msg = str(e)
                    match = re.search(r"no such collation sequence: (\S+)", msg)
                    if match:
                        col_name = match.group(1).strip("'\"")
                        if col_name not in registered_collations:
                            log(f"registering missing collation: {col_name}")
                            conn.close()
                            conn = sqlite3.connect(db_path)
                            registered_collations.add(col_name)
                            for c in registered_collations:
                                conn.create_collation(c, collation_func)
                            continue # Retry the whole iterdump
                    
                    log(f"error during iterdump on attempt {attempt}: {e}")
                    return False

    except Exception as e:
        log(f"error connecting or dumping: {e}")
        return False
    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Git clean filter for SQLite")
    parser.add_argument("db_file", help="Path to the SQLite database file")
    parser.add_argument("--float-precision", type=int, help="Round floats to X digits")
    parser.add_argument("--data-only", action="store_true", help="Output only INSERT statements")
    parser.add_argument("--schema-only", action="store_true", help="Output only schema (no INSERTs)")
    
    args = parser.parse_args()
    db_file = args.db_file

    # --- 1. Robust Path: Atomic Backup + iterdump ---
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        # Use CLI for backup as it's the most robust way to handle locks/WAL
        res_bak = subprocess.run(
            ["sqlite3", "-init", "/dev/null", "-batch", db_file, f".backup '{tmp_path}'"],
            capture_output=True, check=False
        )

        if res_bak.returncode == 0:
            if stream_dump(tmp_path, args):
                return
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # --- 2. Fail Fallback: True Ignore (Index/HEAD) ---
    log(f"warning: ignoring {db_file}, potentially locked or inaccessible")

    # Try Index (:0:)
    res_index = subprocess.run(
        ["git", "show", f":0:{db_file}"], capture_output=True, check=False
    )
    if res_index.returncode == 0:
        sys.stdout.buffer.write(res_index.stdout)
        return

    # Try HEAD
    res_head = subprocess.run(
        ["git", "show", f"HEAD:{db_file}"], capture_output=True, check=False
    )
    if res_head.returncode == 0:
        sys.stdout.buffer.write(res_head.stdout)
        return

    # --- 3. Total Fail: Fallback to Binary ---
    log(f"error: {db_file} is new and locked; falling back to binary")
    with open(db_file, "rb") as f:
        sys.stdout.buffer.write(f.read())


if __name__ == "__main__":
    main()
