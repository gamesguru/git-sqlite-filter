#!/usr/bin/env python3
"""Git clean filter for SQLite databases."""

import argparse
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

from .utils import (
    collation_func,
    extract_missing_collation,
    get_common_args,
    should_skip_submodule,
)

# Handle broken pipes (e.g. | head) without stack trace (Unix only)
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-clean]"


def log(msg):
    """Write a message to stderr with the tool prefix."""
    sys.stderr.write(f"{TOOL} {msg}\n")


def format_sql_value(val, float_precision=None):
    """Format a Python value into its SQLite literal representation."""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        if float_precision is not None and isinstance(val, float):
            # Normalize floats to prevent ghost diffs
            v_str = format(val, f".{float_precision}f").rstrip("0").rstrip(".")
            return v_str or "0.0"
        return str(val)
    if isinstance(val, bytes):
        return f"X'{val.hex().upper()}'"
    # String escaping
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def get_table_metadata(conn, table_name, debug=False):
    """Identify insertable columns and primary keys for stable sorting."""
    try:
        # hidden values: 0=normal, 1=hidden, 2=virtual generated, 3=stored generated
        xinfo = conn.execute(f"PRAGMA table_xinfo('{table_name}')").fetchall()

        insertable = []
        pk_cols = []
        for col in xinfo:
            # col: [id, name, type, notnull, dflt_value, pk, hidden]
            if col[6] == 0:
                insertable.append(col[1])
            if col[5] > 0:
                pk_cols.append(col[1])

        # Fallback for old SQLite or weird virtual tables if xinfo is empty
        if not insertable:
            info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
            insertable = [c[1] for c in info]
            pk_cols = [c[1] for c in info if c[5] > 0]

        if debug:
            log(f"Metadata for {table_name}: {len(insertable)} cols, PKs: {pk_cols}")
        return insertable, pk_cols
    except sqlite3.OperationalError:
        # Fallback for very old SQLite
        info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return [c[1] for c in info], [c[1] for c in info if c[5] > 0]


class DatabaseDumper:
    """Handles parsing and dumping of SQLite database content."""

    def __init__(self, db_path, args, debug=False):
        self.db_path = db_path
        self.args = args
        self.debug = debug
        self.registered_collations = set()
        self.conn = self._connect()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        for col in self.registered_collations:
            conn.create_collation(col, collation_func)
        return conn

    def _ensure_collation(self, error_msg):
        """Register missing collation and reconnect if needed."""
        col_name = extract_missing_collation(error_msg)
        if col_name and col_name not in self.registered_collations:
            if self.debug:
                log(f"registering missing collation: {col_name}")
            self.registered_collations.add(col_name)
            self.conn.close()
            self.conn = self._connect()
            return True
        return False

    def _find_shadow_tables(self):
        """Identify actual FTS shadow tables by scanning virtual table definitions."""
        shadow_tables = set()
        # Scan for FTS3/4/5 tables
        vtabs = self.conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql LIKE '%VIRTUAL TABLE%USING fts%'"
        ).fetchall()

        for name, sql in vtabs:
            sql_upper = sql.upper()
            self._analyze_virtual_table(name, sql_upper, shadow_tables)

        if self.debug and shadow_tables:
            log(f"identified shadow tables: {shadow_tables}")
        return shadow_tables

    def _analyze_virtual_table(self, name, sql_upper, shadow_tables):
        """Analyze a virtual table definition to find its shadow tables."""
        if "USING FTS5" in sql_upper:
            # FTS5 creates: _data, _idx, _content, _docsize, _config
            suffixes = ["_data", "_idx", "_content", "_docsize", "_config"]
        elif "USING FTS3" in sql_upper or "USING FTS4" in sql_upper:
            # FTS3/4 creates: _content, _segments, _segdir, _docsize, _stat
            suffixes = ["_content", "_segments", "_segdir", "_docsize", "_stat"]
        else:
            return

        for suffix in suffixes:
            shadow_name = f"{name}{suffix}"
            shadow_tables.add(shadow_name)

    def dump(self):
        """Perform the full semantic dump."""
        try:
            shadow_tables = self._find_shadow_tables()
            self._dump_header()
            self._dump_schema(shadow_tables)
            self._dump_data(shadow_tables)
            self._dump_footer(shadow_tables)
            return True

        # pylint: disable=broad-exception-caught
        except Exception as e:
            log(f"dump failed: {e}")
            return False

    def _dump_header(self):
        if not self.args.data_only:
            user_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if self.debug:
                log(f"user_version: {user_version}")
            sys.stdout.write(f"PRAGMA user_version = {user_version};\n")
            sys.stdout.write("PRAGMA foreign_keys=OFF;\n")
            sys.stdout.write("BEGIN TRANSACTION;\n")

    def _dump_schema(self, shadow_tables):
        if self.args.data_only:
            return

        objects = self.conn.execute("""
            SELECT name, type, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            AND type IN ('table', 'view')
            ORDER BY name ASC
        """).fetchall()

        if self.debug:
            log(f"discovered {len(objects)} tables/views")

        for obj in objects:
            if obj["name"] in shadow_tables:
                if self.debug:
                    log(f"skipping shadow table schema: {obj['name']}")
                continue

            if obj["sql"]:
                sql = obj["sql"].strip()
                if not sql.endswith(";"):
                    sql += ";"
                sys.stdout.write(f"{sql}\n")

    def _dump_data(self, shadow_tables):
        if self.args.schema_only:
            return

        objects = self.conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name ASC
        """).fetchall()

        for obj in objects:
            if obj["name"] in shadow_tables:
                continue
            self._dump_table_data(obj["name"])

    def _dump_footer(self, _shadow_tables):
        if not self.args.data_only:
            self._dump_extras()
            sys.stdout.write("COMMIT;\n")

    def _dump_table_data(self, table_name):
        """Stream sorted rows for a given table."""
        # Skip Virtual Tables
        sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table_name,)
        ).fetchone()
        if sql and "CREATE VIRTUAL TABLE" in sql["sql"].upper():
            if self.debug:
                log(f"skipping data for virtual table: {table_name}")
            return

        cols, pks = get_table_metadata(self.conn, table_name, self.debug)
        if not cols:
            if self.debug:
                log(f"skipping data for {table_name} (no insertable columns)")
            return

        # Improved Determinism: Sort by PKs or fall back to all columns
        if pks:
            pk_list = ", ".join(f'"{pk}"' for pk in pks)
            order_by = f"ORDER BY {pk_list}"
        else:
            all_cols = ", ".join(f'"{c}"' for c in cols)
            order_by = f"ORDER BY {all_cols}"

        col_list = ", ".join(f'"{c}"' for c in cols)

        if self.debug:
            log(f"dumping table: {table_name}, columns: [{col_list}], sort: {order_by}")

        while True:
            try:
                # Use rowid if no PKs/insertable cols match logic
                cursor = self.conn.execute(
                    f'SELECT {col_list} FROM "{table_name}" {order_by}'
                )
                for row in cursor:
                    vals = [format_sql_value(v, self.args.float_precision) for v in row]
                    sys.stdout.write(
                        f'INSERT INTO "{table_name}" ({col_list}) '
                        f"VALUES ({', '.join(vals)});\n"
                    )
                break
            except sqlite3.OperationalError as e:
                if not self._ensure_collation(e):
                    raise

    def _dump_extras(self, _shadow_tables=None):
        """Dump triggers, indexes, and autoincrement sequences."""
        # Triggers and Indexes (excluding auto-indexes)
        extras = self.conn.execute("""
            SELECT sql FROM sqlite_master
            WHERE type IN ('index', 'trigger')
            AND sql IS NOT NULL
            AND name NOT LIKE 'sqlite_autoindex_%'
        """).fetchall()
        for extra in extras:
            sql = extra[0].strip()
            if not sql.endswith(";"):
                sql += ";"
            sys.stdout.write(f"{sql}\n")

        # Autoincrement (sqlite_sequence)
        has_seq = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
        ).fetchone()
        if has_seq:
            sys.stdout.write('DELETE FROM "sqlite_sequence";\n')
            seq_rows = self.conn.execute(
                'SELECT name, seq FROM "sqlite_sequence" ORDER BY name ASC'
            ).fetchall()
            for row in seq_rows:
                sys.stdout.write(
                    f'INSERT INTO "sqlite_sequence" (name, seq) '
                    f"VALUES ('{row[0]}', {row[1]});\n"
                )


def maybe_warn():
    """Print a safety warning if not printed recently (5s debounce)."""
    sentinel = os.path.join(tempfile.gettempdir(), "git_sqlite_warn_lock")
    try:
        # Check if warning was shown recently
        if os.path.exists(sentinel) and (time.time() - os.path.getmtime(sentinel) < 5):
            return

        log(
            "WARNING: YOU CAN EASILY LOSE DATA IF YOU ISSUE WRITE COMMANDS!!! "
            "(using offline copy is recommended)"
        )

        # Update timestamp
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass  # Ignore permissions/IO errors


def debug_versions(db_file):
    """Log version information for debugging."""
    log(f"starting semantic clean for {db_file}")
    log(f"sqlite3 runtime version: {sqlite3.sqlite_version}")
    try:
        cli_ver = subprocess.check_output(["sqlite3", "--version"], text=True).strip()
        log(f"sqlite3 binary version: {cli_ver}")
    except FileNotFoundError:
        log(
            "sqlite3 binary version: NOT FOUND "
            "(needed for locked databases; 'backup' will fail)"
        )
    # pylint: disable=broad-exception-caught
    except Exception as e:
        log(f"sqlite3 binary version: error getting version ({e})")


def check_fast_path(db_file, debug=False):
    """Check if we can use the fast pass-through path (not an sqlite file)."""
    try:
        with open(db_file, "rb") as f:
            header = f.read(16)
            if header != b"SQLite format 3\x00":
                if debug:
                    log(f"magic header mismatch: {header!r}; falling back to binary")
                sys.stdout.buffer.write(header)
                shutil.copyfileobj(f, sys.stdout.buffer)
                return True
    except OSError:
        pass
    # pylint: disable=broad-exception-caught
    except Exception as e:
        if debug:
            log(f"unexpected error checking header: {e}")
    return False


def run_backup(db_file, tmp_path, debug=False):
    """Run sqlite3 backup command."""
    backup_cmd = [
        "sqlite3",
        "-cmd",
        "PRAGMA busy_timeout=100;",
        "-init",
        "/dev/null",
        "-batch",
        db_file,
        f".backup '{tmp_path}'",
    ]
    if debug:
        log(f"running backup command: {' '.join(backup_cmd)}")
    try:
        return subprocess.run(backup_cmd, capture_output=True, check=False, timeout=5)
    except subprocess.TimeoutExpired:
        log("backup command timed out")
        return subprocess.CompletedProcess(backup_cmd, 1, stderr=b"timeout")


def fallback_dump(db_file, debug=False):
    """Fallback strategies if main dump fails."""
    # 1. Check if it's already a SQL dump (e.g. double smudging)
    try:
        with open(db_file, "rb") as f:
            header = f.read(16)
        if header != b"SQLite format 3\0":
            if debug:
                log(f"file {db_file} is not a SQLite database; passing through")
            with open(db_file, "rb") as f:
                shutil.copyfileobj(f, sys.stdout.buffer)
            return
    # pylint: disable=broad-exception-caught
    except Exception as e:
        if debug:
            log(f"failed to check header for {db_file}: {e}")

    # 2. Use git history (index)
    log(f"warning: using git history for {db_file} (database locked/modified)")
    try:
        res_git = subprocess.run(
            ["git", "show", f":{db_file}"],
            capture_output=True,
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        log(f"git show timed out for {db_file}")
        res_git = subprocess.CompletedProcess([], 1)

    if res_git.returncode == 0:
        sys.stdout.buffer.write(res_git.stdout)
        return

    # 3. Ultimate fallback: Binary read
    log(f"error: {db_file} is inaccessible; using binary raw read")
    with open(db_file, "rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer)


def main():
    """Entry point for git-sqlite-clean."""
    parser = argparse.ArgumentParser(description="Git clean filter for SQLite")
    parser.add_argument("db_file", help="Path to the SQLite database file")
    parser.add_argument("--float-precision", type=int, help="Round floats to X digits")
    parser.add_argument(
        "--data-only", action="store_true", help="Output only INSERT statements"
    )
    parser.add_argument(
        "--schema-only", action="store_true", help="Output only schema (no INSERTs)"
    )

    args = get_common_args(parser)
    debug = args.debug or os.environ.get("GIT_TRACE") in ("1", "true", "2")

    if debug:
        debug_versions(args.db_file)

    if check_fast_path(args.db_file, debug):
        return

    if should_skip_submodule(TOOL):
        # Fast binary pass-through using streaming I/O
        try:
            with open(args.db_file, "rb") as f:
                shutil.copyfileobj(f, sys.stdout.buffer)
        except OSError as e:
            log(f"error reading file in fast-path: {e}")
            sys.exit(1)
        return

    maybe_warn()

    # Use a temporary backup for consistency and lock avoidance
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        res = run_backup(args.db_file, tmp_path, debug)

        if res.returncode == 0:
            with DatabaseDumper(tmp_path, args, debug=debug) as dumper:
                if dumper.dump():
                    return
        else:
            err = res.stderr.decode().strip()
            if "database is locked" not in err:
                log(f"backup failed: {err}")
            fallback_dump(args.db_file, debug)

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    main()
