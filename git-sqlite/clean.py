#!/usr/bin/env python3
import argparse
import os
import re
import signal
import subprocess
import sys
import tempfile

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


def stream_dump(db_path, args):
    """Stream logical SQL dump with filtering and normalization."""
    # 1. PRAGMA user_version (Small, we can just get it)
    try:
        res_ver = subprocess.run(
            ["sqlite3", "-init", "/dev/null", "-batch", db_path, "PRAGMA user_version;"],
            capture_output=True, text=True, check=True
        )
        user_version = res_ver.stdout.strip()
    except subprocess.CalledProcessError:
        user_version = "0"

    # 2. Start .dump process
    cmd = ["sqlite3", "-init", "/dev/null", "-batch", db_path, ".dump"]
    
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )

    try:
        if not args.data_only:
            sys.stdout.write(f"PRAGMA user_version = {user_version};\n")

        for line in proc.stdout:
            # Filtering
            if args.data_only and not line.startswith("INSERT INTO"):
                continue
            if args.schema_only and line.startswith("INSERT INTO"):
                continue
            
            # Normalization
            if args.float_precision is not None:
                line = normalize_floats(line, args.float_precision)
            
            sys.stdout.write(line)

        proc.wait()
        if proc.returncode != 0:
            return False
        return True
    except Exception as e:
        log(f"error during streaming: {e}")
        proc.kill()
        return False


def main():
    parser = argparse.ArgumentParser(description="Git clean filter for SQLite")
    parser.add_argument("db_file", help="Path to the SQLite database file")
    parser.add_argument("--float-precision", type=int, help="Round floats to X digits")
    parser.add_argument("--data-only", action="store_true", help="Output only INSERT statements")
    parser.add_argument("--schema-only", action="store_true", help="Output only schema (no INSERTs)")
    
    args = parser.parse_args()
    db_file = args.db_file

    # --- 1. Robust Path: Atomic Backup + Streaming ---
    # We backup to a temp file first to ensure we have a consistent, unlocked snapshot.
    # This also prevents "database is locked" errors from being streamed to stdout.
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
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

    # --- 2. Fast Path Fallback: Only if Backup Fails (e.g. storage issues) ---
    # This might still leak errors, but we only hit it if the robust path fails.
    if stream_dump(db_file, args):
        return

    # --- 3. Fail Fallback: True Ignore (Index/HEAD) ---
    log(f"warning: ignoring {db_file}, potentially locked or inaccessible")

    # Try Index (:0:)
    res_index = subprocess.run(
        ["git", "show", f":0:{db_file}"], capture_output=True, check=False
    )
    if res_index.returncode == 0:
        # Note: Fallback doesn't support streaming/normalization easily 
        # as it's a binary blob in the index/head usually, but here 
        # we just pass it through.
        sys.stdout.buffer.write(res_index.stdout)
        return

    # Try HEAD
    res_head = subprocess.run(
        ["git", "show", f"HEAD:{db_file}"], capture_output=True, check=False
    )
    if res_head.returncode == 0:
        sys.stdout.buffer.write(res_head.stdout)
        return

    # --- 4. Total Fail: Fallback to Binary ---
    log(f"error: {db_file} is new and locked; falling back to binary")
    with open(db_file, "rb") as f:
        sys.stdout.buffer.write(f.read())


if __name__ == "__main__":
    main()
