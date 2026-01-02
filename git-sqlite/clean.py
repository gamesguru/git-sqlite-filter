#!/usr/bin/env python3
import argparse
import os
import re
import signal
import sqlite3
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


def collation_func(s1, s2):
    # Dummy collation: sort lexicographically
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


def stream_dump(db_path, args):
    """Stream logical SQL dump with noise reduction (sorting) and FTS5 support."""
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Register dummy collations for stable dumps
        registered_collations = set()

        def get_conn():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            for col in registered_collations:
                c.create_collation(col, collation_func)
            return c

        conn = get_conn()

        # 1. PRAGMA user_version
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if not args.data_only:
            sys.stdout.write(f"PRAGMA user_version = {user_version};\n")
            sys.stdout.write("PRAGMA foreign_keys=OFF;\n")

        # 2. Get all tables and views, excluding internal shadow tables
        # We manually build the dump to ensure stable sorting (Noise Reduction)
        objects = conn.execute(
            """
            SELECT name, type, sql FROM sqlite_master 
            WHERE name NOT LIKE 'sqlite_%'
            AND type IN ('table', 'view')
            ORDER BY name ASC
        """
        ).fetchall()

        if not args.data_only:
            sys.stdout.write("BEGIN TRANSACTION;\n")
            for obj in objects:
                # Filter out auto-generated FTS5 shadow tables from schema dump
                if re.search(r"_(content|data|idx|docsize|config)$", obj["name"]):
                    continue
                if obj["sql"]:
                    sys.stdout.write(f"{obj['sql']};\n")

        # 3. Dump Data with Noise Reduction (Sorting)
        if not args.schema_only:
            for obj in objects:
                if obj["type"] != "table":
                    continue

                table_name = obj["name"]
                # Skip FTS5 shadow tables and other internal tables for data dump
                if re.search(r"_(content|data|idx|docsize|config)$", table_name):
                    continue

                # Check if it's a virtual table
                is_virtual = obj["sql"] and "VIRTUAL" in obj["sql"].upper()

                # Discover Columns and Primary Key for stable sorting
                # Using table_xinfo to detect generated/hidden columns (hidden column is at index 6)
                # hidden values: 0=normal, 1=hidden, 2=virtual generated, 3=stored generated
                xinfo = conn.execute(f"PRAGMA table_xinfo('{table_name}')").fetchall()
                insertable_cols = []
                pk_cols = []
                for col in xinfo:
                    # col: [id, name, type, notnull, dflt_value, pk, hidden]
                    if (
                        col[6] == 0
                    ):  # Only non-hidden/non-generated columns are insertable
                        insertable_cols.append(col[1])
                    if col[5] > 0:  # Primary Key
                        pk_cols.append(col[1])

                order_by = (
                    f"ORDER BY {', '.join(f'\"{pk}\"' for pk in pk_cols)}"
                    if pk_cols
                    else ""
                )

                while True:
                    try:
                        # We only select insertable columns to avoid issues with generated columns
                        col_list = ", ".join(f'"{n}"' for n in insertable_cols)
                        cursor = conn.execute(
                            f'SELECT {col_list} FROM "{table_name}" {order_by}'
                        )

                        for row in cursor:
                            vals = []
                            for i, val in enumerate(row):
                                if val is None:
                                    vals.append("NULL")
                                elif isinstance(val, (int, float)):
                                    if args.float_precision is not None and isinstance(
                                        val, float
                                    ):
                                        v_str = (
                                            format(val, f".{args.float_precision}f")
                                            .rstrip("0")
                                            .rstrip(".")
                                        )
                                        vals.append(v_str or "0.0")
                                    else:
                                        vals.append(str(val))
                                elif isinstance(val, bytes):
                                    vals.append(f"X'{val.hex().upper()}'")
                                else:
                                    # String escaping
                                    escaped = str(val).replace("'", "''")
                                    vals.append(f"'{escaped}'")

                            sys.stdout.write(
                                f"INSERT INTO \"{table_name}\" ({col_list}) VALUES ({', '.join(vals)});\n"
                            )
                        break  # Success
                    except sqlite3.OperationalError as e:
                        msg = str(e)
                        match = re.search(r"no such collation sequence: (\S+)", msg)
                        if match:
                            col_name = match.group(1).strip("'\"")
                            if col_name not in registered_collations:
                                log(f"registering missing collation: {col_name}")
                                registered_collations.add(col_name)
                                conn.close()
                                conn = get_conn()
                                continue
                        raise

        if not args.data_only:
            # Re-add triggers and indexes at the end
            # Excluding internal indexes auto-created by SQLite
            extras = conn.execute(
                """
                SELECT sql FROM sqlite_master 
                WHERE type IN ('index', 'trigger') 
                AND sql IS NOT NULL
                AND name NOT LIKE 'sqlite_autoindex_%'
            """
            ).fetchall()
            for extra in extras:
                sys.stdout.write(f"{extra[0]};\n")

            # --- PRESERVE AUTOINCREMENT COUNTERS ---
            # We dump sqlite_sequence data so next IDs aren't reused (Semantic preservation)
            has_seq = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
            ).fetchone()
            if has_seq:
                sys.stdout.write('DELETE FROM "sqlite_sequence";\n')
                seq_rows = conn.execute(
                    'SELECT name, seq FROM "sqlite_sequence" ORDER BY name ASC'
                ).fetchall()
                for row in seq_rows:
                    sys.stdout.write(
                        f"INSERT INTO \"sqlite_sequence\" (name, seq) VALUES ('{row[0]}', {row[1]});\n"
                    )

            sys.stdout.write("COMMIT;\n")

        return True

    except Exception as e:
        log(f"error during noise-reduction dump: {e}")
        return False
    finally:
        if conn:
            conn.close()


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
    db_file = args.db_file

    # --- 1. Robust Path: Atomic Backup + iterdump ---
    with tempfile.NamedTemporaryFile(
        prefix="sqlite_bak_", suffix=".sqlite", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        # Use CLI for backup as it's the most robust way to handle locks/WAL
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
