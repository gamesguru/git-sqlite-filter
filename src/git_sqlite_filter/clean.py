#!/usr/bin/env python3
import argparse
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

# Handle broken pipes (e.g. | head) without stack trace
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

TOOL = "[git-sqlite-clean]"


def log(msg):
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


def collation_func(s1, s2):
    """Dumb lexicographical sort for unregistered collations."""
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


class DatabaseDumper:
    def __init__(self, db_path, args, debug=False):
        self.db_path = db_path
        self.args = args
        self.debug = debug
        self.registered_collations = set()
        self.conn = self._connect()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        for col in self.registered_collations:
            conn.create_collation(col, collation_func)
        return conn

    def _ensure_collation(self, error_msg):
        """Register missing collation and reconnect if needed."""
        match = re.search(r"no such collation sequence: (\S+)", str(error_msg))
        if match:
            col_name = match.group(1).strip("'\"")
            if col_name not in self.registered_collations:
                if self.debug:
                    log(f"registering missing collation: {col_name}")
                self.registered_collations.add(col_name)
                self.conn.close()
                self.conn = self._connect()
                return True
        return False

    def dump(self):
        """Perform the full semantic dump."""
        try:
            # 1. Metadata / Versioning
            user_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if not self.args.data_only:
                if self.debug:
                    log(f"user_version: {user_version}")
                sys.stdout.write(f"PRAGMA user_version = {user_version};\n")
                sys.stdout.write("PRAGMA foreign_keys=OFF;\n")
                sys.stdout.write("BEGIN TRANSACTION;\n")

            # 2. Schema Discovery
            objects = self.conn.execute(
                """
                SELECT name, type, sql FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                AND type IN ('table', 'view')
                ORDER BY name ASC
            """
            ).fetchall()

            if self.debug:
                log(f"discovered {len(objects)} tables/views")

            # 3. Output Schema (Tables/Views)
            if not self.args.data_only:
                for obj in objects:
                    # Skip auto-generated FTS5 shadow tables
                    if re.search(r"_(content|data|idx|docsize|config)$", obj["name"]):
                        if self.debug:
                            log(f"skipping shadow table schema: {obj['name']}")
                        continue
                    if obj["sql"]:
                        # Ensure we have a semicolon and newline
                        sql = obj["sql"].strip()
                        if not sql.endswith(";"):
                            sql += ";"
                        sys.stdout.write(f"{sql}\n")

            # 4. Output Data (Sorted for Noise Reduction)
            if not self.args.schema_only:
                for obj in [o for o in objects if o["type"] == "table"]:
                    if re.search(r"_(content|data|idx|docsize|config)$", obj["name"]):
                        continue
                    self._dump_table_data(obj["name"])

            # 5. Finalize (Indexes/Triggers/Sequences)
            if not self.args.data_only:
                self._dump_extras()
                sys.stdout.write("COMMIT;\n")

            return True

        except Exception as e:
            log(f"dump failed: {e}")
            # Don't output ANYTHING on failure
            return False
        finally:
            if self.conn:
                self.conn.close()

    def _dump_table_data(self, table_name):
        """Stream sorted rows for a given table."""
        cols, pks = get_table_metadata(self.conn, table_name, self.debug)
        if not cols:
            if self.debug:
                log(f"skipping data for {table_name} (no insertable columns)")
            return

        # FTS5 tables often don't have PKs, but they have rowid
        if not pks:
            pks = ["rowid"]

        pk_list = ", ".join(f'"{pk}"' for pk in pks)
        order_by = f"ORDER BY {pk_list}"
        col_list = ", ".join(f'"{c}"' for c in cols)

        if self.debug:
            log(f"dumping table: {table_name}, columns: [{col_list}], sort: {order_by}")

        while True:
            try:
                # We use rowid if no PKs, but don't include it in col_list unless it was explicitly insertable
                cursor = self.conn.execute(
                    f'SELECT {col_list} FROM "{table_name}" {order_by}'
                )
                for row in cursor:
                    vals = [format_sql_value(v, self.args.float_precision) for v in row]
                    sys.stdout.write(
                        f"INSERT INTO \"{table_name}\" ({col_list}) VALUES ({', '.join(vals)});\n"
                    )
                break
            except sqlite3.OperationalError as e:
                # Catch "no such column: rowid" for some virtual tables without rowids
                if "no such column: rowid" in str(e) and pks == ["rowid"]:
                    if self.debug:
                        log(f"rewriting query for {table_name} (no rowid support)")
                    try:
                        cursor = self.conn.execute(
                            f'SELECT {col_list} FROM "{table_name}"'
                        )
                        for row in cursor:
                            vals = [
                                format_sql_value(v, self.args.float_precision)
                                for v in row
                            ]
                            sys.stdout.write(
                                f"INSERT INTO \"{table_name}\" ({col_list}) VALUES ({', '.join(vals)});\n"
                            )
                        break
                    except Exception as e2:
                        raise e2

                if not self._ensure_collation(e):
                    raise

    def _dump_extras(self):
        """Dump triggers, indexes, and autoincrement sequences."""
        # Triggers and Indexes (excluding auto-indexes)
        extras = self.conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type IN ('index', 'trigger')
            AND sql IS NOT NULL
            AND name NOT LIKE 'sqlite_autoindex_%'
        """
        ).fetchall()
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
                    f"INSERT INTO \"sqlite_sequence\" (name, seq) VALUES ('{row[0]}', {row[1]});\n"
                )


def main():
    parser = argparse.ArgumentParser(description="Git clean filter for SQLite")
    parser.add_argument("db_file", help="Path to the SQLite database file")
    parser.add_argument("--float-precision", type=int, help="Round floats to X digits")
    parser.add_argument(
        "--data-only", action="store_true", help="Output only INSERT statements"
    )
    parser.add_argument(
        "--schema-only", action="store_true", help="Output only schema (no INSERTs)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log debug info to stderr (also triggered by GIT_TRACE=1)",
    )
    args = parser.parse_args()

    debug = args.debug or os.environ.get("GIT_TRACE") in ("1", "true", "2")

    if debug:
        log(f"starting semantic clean for {args.db_file}")
        log(f"sqlite3 library version: {sqlite3.version}")
        log(f"sqlite3 runtime version: {sqlite3.sqlite_version}")
        try:
            cli_ver = subprocess.check_output(
                ["sqlite3", "--version"], text=True
            ).strip()
            log(f"sqlite3 binary version: {cli_ver}")
        except FileNotFoundError:
            log(
                "sqlite3 binary version: NOT FOUND (needed for locked databases; 'backup' will fail)"
            )
        except Exception as e:
            log(f"sqlite3 binary version: error getting version ({e})")

    # Safety warning with 5s debounce (User Request)
    sentinel = os.path.join(tempfile.gettempdir(), "git_sqlite_warn_lock")
    try:
        if not os.path.exists(sentinel) or (
            time.time() - os.path.getmtime(sentinel) > 5
        ):
            log("WARNING: YOU CAN EASILY LOSE DATA IF YOU ISSUE WRITE COMMANDS!!!")
            log("TO KEEP YOUR DATA SAFE, USE GIT FROM A USER WITH READ-ONLY ACCESS!!!")
            with open(sentinel, "w") as f:
                f.write(str(time.time()))
    except OSError:
        pass  # Ignore permissions/IO errors

    # Use a temporary backup for consistency and lock avoidance
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        # Step 1: Backup (CLI is most robust for WAL/Locks)
        # We rely on the CLI failing fast on locks so we can fallback to git history
        backup_cmd = [
            "sqlite3",
            "-init",
            "/dev/null",
            "-batch",
            args.db_file,
            f".backup '{tmp_path}'",
        ]
        if debug:
            log(f"running backup command: {' '.join(backup_cmd)}")
        res = subprocess.run(backup_cmd, capture_output=True, check=False)

        if res.returncode == 0:
            # Step 2: Semantic Dump
            dumper = DatabaseDumper(tmp_path, args, debug=debug)
            if dumper.dump():
                return
        else:
            err = res.stderr.decode().strip()
            if "database is locked" not in err:
                log(f"backup failed: {err}")

        # Fallback to Index/HEAD if backup/dump fails
        log(
            f"warning: falling back to git history for {args.db_file} (database locked/modified)"
        )
        for ref in [f":0:{args.db_file}", f"HEAD:{args.db_file}"]:
            res_git = subprocess.run(
                ["git", "show", ref], capture_output=True, check=False
            )
            if res_git.returncode == 0:
                sys.stdout.buffer.write(res_git.stdout)
                return

        # Ultimate fallback: Binary raw read
        log(f"error: {args.db_file} is inaccessible; using binary raw read")
        with open(args.db_file, "rb") as f:
            sys.stdout.buffer.write(f.read())

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    main()
