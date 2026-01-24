#!/usr/bin/env python3
"""Git smudge filter for SQLite databases."""

import argparse
import os
import re
import shutil
import signal
import sqlite3
import sys
import tempfile

from .utils import (
    collation_func,
    get_common_args,
    should_skip_submodule,
)

# Handle broken pipes (e.g. | head) without stack trace (Unix only)
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-smudge]"


def log(msg):
    """Write a message to stderr with the tool prefix."""
    sys.stderr.write(f"{TOOL} {msg}\n")


def _is_fts5_trigger(statement_upper):
    """Check if statement is an internal FTS5 trigger."""
    if "CREATE TRIGGER" not in statement_upper:
        return False
    # Regex to find the table name the trigger is ON
    match = re.search(r"ON\s+[\"']?([a-zA-Z0-9_]+)[\"']?", statement_upper)
    if match:
        table_name = match.group(1)
        if any(
            table_name.endswith(x)
            for x in ("_CONTENT", "_DOC", "_CONFIG", "_IDX", "_DATA")
        ):
            return True
    return False


def _should_suppress_statement(statement, debug=False):
    """Determine if a SQL statement should be filtered out.
    Extracted from filter_sql_stream to reduce cyclomatic complexity."""
    upper = statement.upper().strip()

    if "PRAGMA WRITABLE_SCHEMA" in upper:
        return True

    if _is_fts5_trigger(upper):
        if debug:
            log("skipping FTS5 internal trigger")
        return True

    if "ROLLBACK" in upper and "ROLLBACK TO" not in upper:
        log("warning: skipping ROLLBACK in dump (corrupted input?)")
        return True

    if ("INSERT INTO" in upper) and (
        "SQLITE_MASTER" in upper or "SQLITE_STAT" in upper
    ):
        return True

    if upper.startswith(("BEGIN TRANSACTION", "COMMIT", "ROLLBACK")):
        parts = upper.strip(";").split()
        if len(parts) < 3:
            return True

    return False


def filter_sql_stream(stream, debug=False):
    """Filter out problematic statements but preserve as much as possible.
    This filter is standalone and yields its own setup/transaction wrappers."""
    yield "PRAGMA foreign_keys=OFF;\n"
    yield "BEGIN TRANSACTION;\n"

    buffer = []
    for line in stream:
        buffer.append(line)
        current_block = "".join(buffer)

        if sqlite3.complete_statement(current_block):
            statement = current_block
            buffer = []

            if not _should_suppress_statement(statement, debug):
                yield statement

    if buffer:
        final = "".join(buffer)
        if final.strip():
            yield final

    yield "COMMIT;\n"


class DatabaseRestorer:
    """Handles parsing and restoring of SQLite database from SQL dump."""

    def __init__(self, debug=False):
        self.registered_collations = set()
        self.tmp_path = None
        self.conn = None
        self.debug = debug

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cleanup()
        if self.conn:
            self.conn.close()

    def restore(self, sql_script_source):
        """Restore database from SQL iterator or string with collation discovery."""
        if isinstance(sql_script_source, str):
            # Wrap string in a list to make it an iterable of one statement
            sql_script_source = [sql_script_source]

        # Step 1: Stream the SQL to a temporary file
        fd_sql, sql_tmp_path = tempfile.mkstemp(
            prefix="sqlite_smudge_sql_", suffix=".sql"
        )
        try:
            with os.fdopen(fd_sql, "w", encoding="utf-8") as f_sql:
                for statement in sql_script_source:
                    f_sql.write(statement)

            return self._restore_loop(sql_tmp_path)
        finally:
            if os.path.exists(sql_tmp_path):
                os.remove(sql_tmp_path)

    def _restore_loop(self, sql_tmp_path):
        """Attempt restoration in a loop to handle dynamic collations."""
        max_retries = 100
        for i in range(max_retries):
            self._create_temp_db()

            if self.debug:
                log(f"restoration attempt {i+1}...")

            success, retry = self._apply_sql_file(sql_tmp_path)

            if success:
                return True

            if self.conn:
                self.conn.close()

            if not retry:
                return False
        return False

    def _apply_sql_file(self, sql_tmp_path):
        """Apply SQL from file to current DB connection."""
        try:
            with open(sql_tmp_path, "r", encoding="utf-8") as f_sql:
                for statement in self._yield_statements(f_sql):
                    if not statement.strip():
                        continue
                    try:
                        self.conn.execute(statement)
                    except sqlite3.OperationalError as e:
                        if self._handle_op_error(e):
                            return False, True
        except sqlite3.OperationalError as e:
            log(f"error: restore failed: {e}")
            return False, False
        return True, False

    def _handle_op_error(self, e):
        """Handle SQLite operational errors during restore."""
        if self.debug:
            log(f"caught operational error: {e}")

        if self._ensure_collation(e):
            return True

        e_str = str(e).lower()
        if "no such table" in e_str or ("index" in e_str and "already exists" in e_str):
            log(f"warning: ignoring error: {e}")
            return False

        raise e

    def _yield_statements(self, file_handle):
        """Yield full SQL statements from a file handle using robust splitting."""
        buffer = []
        for line in file_handle:
            buffer.append(line)
            current_block = "".join(buffer)
            if sqlite3.complete_statement(current_block):
                yield current_block
                buffer = []
        if buffer:
            final = "".join(buffer)
            if final.strip():
                yield final

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
        """Output the rebuilt binary database using streaming I/O."""
        with open(self.tmp_path, "rb") as f:
            shutil.copyfileobj(f, sys.stdout.buffer)

    def cleanup(self):
        """Remove temporary files."""
        if self.tmp_path and os.path.exists(self.tmp_path):
            os.remove(self.tmp_path)


def main():
    """Entry point for git-sqlite-smudge."""
    parser = argparse.ArgumentParser(description="Git smudge filter for SQLite")
    parser.add_argument("db_file", nargs="?", help="Ignored but passed by Git")
    parser.add_argument(
        "--schema", help="Path to a base schema file to apply before data"
    )

    args = get_common_args(parser)

    debug = args.debug or os.environ.get("GIT_TRACE") in ("1", "true", "2")

    # Submodule optimization check: Skip smudge if configured to ignore-submodules
    if should_skip_submodule("git-sqlite-smudge"):
        # Fast pass-through: pipe stdin directly to stdout
        shutil.copyfileobj(sys.stdin.buffer, sys.stdout.buffer)
        return

    # filter_sql_stream is a generator. We combine it with schema if present.
    def get_script_iterator():
        # Schema (Applied outside transaction to avoid locks/complexity with virtual tables)
        if args.schema and os.path.exists(args.schema):
            if debug:
                log(f"loading schema from {args.schema}")
            with open(args.schema, "r", encoding="utf-8") as f:
                yield from filter_sql_stream(f, debug=debug)

        # Data (filter_sql_stream provides its own transaction/setup)
        yield from filter_sql_stream(sys.stdin, debug=debug)

    with DatabaseRestorer(debug=debug) as restorer:
        if restorer.restore(get_script_iterator()):
            restorer.stream_to_stdout()
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
