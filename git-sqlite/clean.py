#!/usr/bin/env python3
import argparse
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile

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


def get_table_metadata(conn, table_name):
    """Identify insertable columns and primary keys for stable sorting."""
    # hidden values: 0=normal, 1=hidden, 2=virtual generated, 3=stored generated
    xinfo = conn.execute(f"PRAGMA table_xinfo('{table_name}')").fetchall()
    insertable = [col[1] for col in xinfo if col[6] == 0]
    pk_cols = [col[1] for col in xinfo if col[5] > 0]
    return insertable, pk_cols


def collation_func(s1, s2):
    """Dumb lexicographical sort for unregistered collations."""
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


class DatabaseDumper:
    def __init__(self, db_path, args):
        self.db_path = db_path
        self.args = args
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

            # 3. Output Schema (Tables/Views)
            if not self.args.data_only:
                for obj in objects:
                    # Skip auto-generated FTS5 shadow tables
                    if re.search(r"_(content|data|idx|docsize|config)$", obj["name"]):
                        continue
                    if obj["sql"]:
                        sys.stdout.write(f"{obj['sql']};\n")

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
            return False
        finally:
            if self.conn:
                self.conn.close()

    def _dump_table_data(self, table_name):
        """Stream sorted rows for a given table."""
        cols, pks = get_table_metadata(self.conn, table_name)
        if not cols:
            return

        order_by = f"ORDER BY {', '.join(f'\"{pk}\"' for pk in pks)}" if pks else ""
        col_list = ", ".join(f'"{c}"' for c in cols)

        while True:
            try:
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
            sys.stdout.write(f"{extra[0]};\n")

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
    args = parser.parse_args()

    # Use a temporary backup for consistency and lock avoidance
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        # Step 1: Backup (CLI is most robust for WAL/Locks)
        backup_cmd = [
            "sqlite3",
            "-init",
            "/dev/null",
            "-batch",
            args.db_file,
            f".backup '{tmp_path}'",
        ]
        res = subprocess.run(backup_cmd, capture_output=True, check=False)

        if res.returncode == 0:
            # Step 2: Semantic Dump
            dumper = DatabaseDumper(tmp_path, args)
            if dumper.dump():
                return

        # Fallback to Index/HEAD if backup/dump fails
        log(f"warning: falling back to git history for {args.db_file}")
        for ref in [f":0:{args.db_file}", f"HEAD:{args.db_file}"]:
            res_git = subprocess.run(
                ["git", "show", ref], capture_output=True, check=False
            )
            if res_git.returncode == 0:
                sys.stdout.buffer.write(res_git.stdout)
                return

        # Ultimate fallback: Binary read
        log(f"error: {args.db_file} is inaccessible; using binary raw read")
        with open(args.db_file, "rb") as f:
            sys.stdout.buffer.write(f.read())

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    main()
