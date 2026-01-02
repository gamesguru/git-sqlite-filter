#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import tempfile

# Handle broken pipes gracefully
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-clean]"


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def get_dump(db_path):
    """Attempt logical SQL dump including user_version."""
    try:
        # 1. Run .dump
        res_dump = subprocess.run(
            ["sqlite3", "-init", "/dev/null", "-batch", db_path, ".dump"],
            capture_output=True,
            text=True,
            check=True,
        )
        # 2. Run PRAGMA user_version
        res_ver = subprocess.run(
            [
                "sqlite3",
                "-init",
                "/dev/null",
                "-batch",
                db_path,
                "PRAGMA user_version;",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        full_dump = (
            res_dump.stdout.strip()
            + f"\nPRAGMA user_version = {res_ver.stdout.strip()};\n"
        )
        return full_dump
    except subprocess.CalledProcessError:
        return None


def main():
    if len(sys.argv) < 2:
        return

    db_file = sys.argv[1]

    # --- 1. Fast Path: Direct Dump ---
    dump = get_dump(db_file)
    if dump:
        sys.stdout.write(dump)
        return

    # --- 2. Locked Path: Atomic Backup ---
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        # Attempt backup
        res_bak = subprocess.run(
            [
                "sqlite3",
                "-init",
                "/dev/null",
                "-batch",
                db_file,
                f".backup '{tmp_path}'",
            ],
            capture_output=True,
            check=False,
        )

        if res_bak.returncode == 0:
            dump = get_dump(tmp_path)
            if dump:
                sys.stdout.write(dump)
                return
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # --- 3. Fail Fallback: True Ignore (Index/HEAD) ---
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

    # --- 4. Total Fail: Fallback to Binary ---
    log(f"error: {db_file} is new and locked; falling back to binary")
    with open(db_file, "rb") as f:
        sys.stdout.buffer.write(f.read())


if __name__ == "__main__":
    main()
