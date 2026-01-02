#!/usr/bin/env python3
import argparse
import os
import re
import signal
import sqlite3
import sys
import tempfile

# Handle broken pipes (e.g. | head) without stack trace
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-smudge]"

# Safety warning to match clean.py
WARNING_MSG = [
    "WARNING: YOU CAN EASILY LOSE DATA IF YOU ISSUE WRITE COMMANDS!!!",
    "TO KEEP YOUR DATA SAFE, USE GIT FROM A USER WITH READ-ONLY ACCESS!!!",
]

# Set of Git sub-commands that can write to the repository
WRITE_CMDS = {
    "checkout",
    "pull",
    "reset",
    "merge",
    "rebase",
    "push",
    "commit",
    "apply",
    "cherry-pick",
    "revert",
}


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def maybe_warn():
    """Smudge is always a write operation (checkout, pull, merge, etc.).

    Prompts for confirmation unless GIT_SQLITE_ALLOW_WRITE=1 is set.
    Uses /dev/tty to bypass stdin pipe.
    """
    # Allow bypass via env var for CI/automation
    if os.environ.get("GIT_SQLITE_ALLOW_WRITE") == "1":
        return

    for line in WARNING_MSG:
        log(line)

    # Try to prompt user via /dev/tty (bypasses stdin pipe)
    try:
        with open("/dev/tty", "r") as tty:
            sys.stderr.write(f"{TOOL} Continue with write operation? [y/N] ")
            sys.stderr.flush()
            response = tty.readline().strip().lower()
            if response != "y":
                log("Aborted by user.")
                sys.exit(1)
    except (OSError, IOError):
        # No TTY available (CI, pipes, etc.) - abort unless env var is set
        log(
            "No TTY available for confirmation. Set GIT_SQLITE_ALLOW_WRITE=1 to proceed."
        )
        sys.exit(1)


def collation_func(s1, s2):
    """Dumb lexicographical sort for unregistered collations."""
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


def filter_sql_stream(stream, debug=False):
    """Filter out problematic statements but preserve as much as possible."""
    yield "PRAGMA foreign_keys=OFF;\n"
    yield "BEGIN TRANSACTION;\n"

    buffer = []

    for line in stream:
        buffer.append(line)

        if line.strip().endswith(";"):
            statement = "".join(buffer)
            buffer = []

            statement_upper = statement.upper().strip()

            # Skip problematic pragmas that shouldn't be in a dump
            if "PRAGMA WRITABLE_SCHEMA" in statement_upper:
                continue

            # Skip FTS5 internal triggers (they auto-recreate on FTS table creation)
            # FTS5 triggers typically: tablename_insert, tablename_delete, tablename_update
            if "CREATE TRIGGER" in statement_upper:
                trigger_name = statement_upper.split()[2]  # CREATE TRIGGER name
                # Heuristic: FTS triggers reference the content/docsize/config tables
                if any(x in statement_upper for x in ("_CONTENT", "_DOC", "_CONFIG", "_IDX", "_DATA")):
                    if debug:
                        log(f"skipping FTS5 internal trigger: {trigger_name}")
                    continue

            # Skip ROLLBACK - if this appears, the dump is corrupted, just skip it
            if "ROLLBACK" in statement_upper and "ROLLBACK TO" not in statement_upper:
                log("warning: skipping ROLLBACK in dump (corrupted input?)")
                continue

            # Skip sqlite_sequence, sqlite_master inserts
            if ("INSERT INTO" in statement_upper) and (
                "SQLITE_MASTER" in statement_upper or "SQLITE_STAT" in statement_upper
            ):
                continue

            # Skip nested transactions
            if statement_upper.startswith(("BEGIN TRANSACTION", "COMMIT")):
                continue

            yield statement

    if buffer:
        yield "".join(buffer)

    yield "COMMIT;\n"


class DatabaseRestorer:
    def __init__(self, debug=False):
        self.registered_collations = set()
        self.tmp_path = None
        self.conn = None
        self.debug = debug

    def restore(self, sql_script):
        """Restore database from SQL script with collation discovery."""
        max_retries = 100

        for i in range(max_retries):
            self._create_temp_db()
            try:
                if self.debug:
                    log(f"restoration attempt {i+1}...")
                self.conn.executescript(sql_script)
                return True
            except sqlite3.OperationalError as e:
                err_msg = str(e).lower()
                if self.debug:
                    log(f"caught operational error: {e}")

                if not self._ensure_collation(e):
                    log(f"error: restore failed: {e}")

                    if "no such table" in err_msg:
                        # Forensic dump of the DB state
                        try:
                            tables = self.conn.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            ).fetchall()
                            log(f"current tables in DB: {[t[0] for t in tables]}")
                            # Check if fts5 is even enabled
                            self.conn.execute(
                                "CREATE VIRTUAL TABLE fts_probe USING fts5(c)"
                            )
                            log("fts5 module seems supported in this python session")
                        except Exception as probe_err:
                            log(f"capability check failed: {probe_err}")

                    if self.debug:
                        log("--- FAILED SQL SCRIPT ---")
                        sys.stderr.write(sql_script)
                        log("--- END FAILED SQL SCRIPT ---")
                    return False
            finally:
                if self.conn:
                    self.conn.close()

    def _create_temp_db(self):
        """Initialize a fresh temporary database with registered collations."""
        if self.tmp_path and os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)

        fd, self.tmp_path = tempfile.mkstemp(prefix="sqlite_smudge_", suffix=".sqlite")
        os.close(fd)

        self.conn = sqlite3.connect(self.tmp_path)
        for col in self.registered_collations:
            self.conn.create_collation(col, collation_func)

    def _ensure_collation(self, error_msg):
        """Discover and register missing collations."""
        match = re.search(r"no such collation sequence: (\S+)", str(error_msg))
        if match:
            col_name = match.group(1).strip("'\"")
            if col_name not in self.registered_collations:
                if self.debug:
                    log(f"registering missing collation: {col_name}")
                self.registered_collations.add(col_name)
                return True
        return False

    def stream_to_stdout(self):
        """Output the rebuilt binary database."""
        with open(self.tmp_path, "rb") as f:
            sys.stdout.buffer.write(f.read())

    def cleanup(self):
        """Remove temporary files."""
        if self.tmp_path and os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)


def main():
    parser = argparse.ArgumentParser(description="Git smudge filter for SQLite")
    parser.add_argument("db_file", nargs="?", help="Ignored but passed by Git")
    parser.add_argument(
        "--schema", help="Path to a base schema file to apply before data"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log debug info to stderr (also triggered by GIT_TRACE=1)",
    )
    args = parser.parse_args()

    debug = args.debug or os.environ.get("GIT_TRACE") in ("1", "true", "2")

    sql_lines = []
    if args.schema and os.path.exists(args.schema):
        if debug:
            log(f"loading schema from {args.schema}")
        with open(args.schema, "r") as f:
            sql_lines.extend(f.readlines())

    if debug:
        log("parsing SQL stream from stdin")

    sql_lines.extend(list(filter_sql_stream(sys.stdin, debug=debug)))
    script = "".join(sql_lines)

    restorer = DatabaseRestorer(debug=debug)
    try:
        if restorer.restore(script):
            restorer.stream_to_stdout()
        else:
            sys.exit(1)
    finally:
        restorer.cleanup()


if __name__ == "__main__":
    main()
