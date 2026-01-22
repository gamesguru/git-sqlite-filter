#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile

# Handle broken pipes (e.g. | head) without stack trace
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-smudge]"


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def collation_func(s1, s2):
    """Dumb lexicographical sort for unregistered collations."""
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


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

            statement_upper = statement.upper().strip()

            # Skip problematic pragmas that shouldn't be in a dump
            if "PRAGMA WRITABLE_SCHEMA" in statement_upper:
                continue

            # Skip FTS5 internal triggers (they auto-recreate on FTS table creation)
            if "CREATE TRIGGER" in statement_upper:
                parts = statement_upper.split()
                if len(parts) > 2:
                    trigger_name = parts[2]
                    if any(
                        x in statement_upper
                        for x in ("_CONTENT", "_DOC", "_CONFIG", "_IDX", "_DATA")
                    ):
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

            # Skip ALL transaction-related statements from the dump (we provide our own)
            if statement_upper.startswith(("BEGIN TRANSACTION", "COMMIT", "ROLLBACK")):
                parts = statement_upper.strip(";").split()
                # "ROLLBACK TO savepoint" has 3 parts. "ROLLBACK" has 1.
                if len(parts) < 3:
                    continue

            yield statement

    if buffer:
        final = "".join(buffer)
        if final.strip():
            yield final

    yield "COMMIT;\n"


class DatabaseRestorer:
    def __init__(self, debug=False):
        self.registered_collations = set()
        self.tmp_path = None
        self.conn = None
        self.debug = debug

    def restore(self, sql_script_source):
        """Restore database from SQL iterator or string with collation discovery."""
        if isinstance(sql_script_source, str):
            # Wrap string in a list to make it an iterable of one statement
            sql_script_source = [sql_script_source]
        # Step 1: Stream the SQL to a temporary file so we can read it multiple times
        # for collation discovery without keeping it all in memory.
        fd_sql, sql_tmp_path = tempfile.mkstemp(
            prefix="sqlite_smudge_sql_", suffix=".sql"
        )
        try:
            with os.fdopen(fd_sql, "w") as f_sql:
                for statement in sql_script_source:
                    f_sql.write(statement)

            # Step 2: Attempt restoration (multi-pass if new collations are found)
            max_retries = 100
            for i in range(max_retries):
                self._create_temp_db()
                try:
                    if self.debug:
                        log(f"restoration attempt {i+1}...")

                    with open(sql_tmp_path, "r") as f_sql:
                        # Use the same filter-like logic to split the file back into statements
                        # (though it's already statement-per-yield from the iterator)
                        # We use a simple generator to re-yield statements from the file.
                        for statement in self._yield_statements(f_sql):
                            if statement.strip():
                                self.conn.execute(statement)
                    return True
                except sqlite3.OperationalError as e:
                    if self.debug:
                        log(f"caught operational error: {e}")
                    if not self._ensure_collation(e):
                        log(f"error: restore failed: {e}")
                        return False
                finally:
                    if self.conn:
                        self.conn.close()
            return False
        finally:
            if os.path.exists(sql_tmp_path):
                os.remove(sql_tmp_path)

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

    # Submodule optimization check: Skip smudge if configured to ignore-submodules
    super_root = get_superproject_root()
    if super_root:
        ignored = get_git_config_bool("sqlite-filter.ignore-submodules")
        if not ignored:
            ignored = get_git_config_bool(
                "sqlite-filter.ignore-submodules", cwd=super_root
            )

        if ignored:
            # Fast pass-through: pipe stdin directly to stdout
            shutil.copyfileobj(sys.stdin.buffer, sys.stdout.buffer)
            return

    # filter_sql_stream is a generator. We combine it with schema if present.
    def get_script_iterator():
        # Schema (Applied outside transaction to avoid locks/complexity with virtual tables)
        if args.schema and os.path.exists(args.schema):
            if debug:
                log(f"loading schema from {args.schema}")
            with open(args.schema, "r") as f:
                yield from filter_sql_stream(f, debug=debug)

        # Data (filter_sql_stream provides its own transaction/setup)
        yield from filter_sql_stream(sys.stdin, debug=debug)

    restorer = DatabaseRestorer(debug=debug)
    try:
        if restorer.restore(get_script_iterator()):
            restorer.stream_to_stdout()
        else:
            sys.exit(1)
    finally:
        restorer.cleanup()


def get_superproject_root():
    """Return the path to the superproject's working tree if in a submodule."""
    # Fast heuristic: In submodules, .git is a file. In regular repos, it's a directory.
    if not os.path.exists(".git") or not os.path.isfile(".git"):
        return None

    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-superproject-working-tree"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out if out else None
    except subprocess.CalledProcessError:
        return None


def get_git_config_bool(key, cwd=None):
    """Get a git config boolean value (true/false) handling various formats."""
    try:
        cmd = ["git"]
        if cwd:
            cmd.extend(["-C", cwd])
        cmd.extend(["config", "--type=bool", "--get", key])

        val = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        return val == "true"
    except subprocess.CalledProcessError:
        return False


if __name__ == "__main__":
    main()
