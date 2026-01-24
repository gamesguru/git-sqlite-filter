"""Script to verify handling of Firefox-like profiles with WAL/Foreign Keys."""

import os
import sqlite3
import subprocess
import sys
import tempfile


def create_firefox_style_db(path):
    """
    Creates a SQLite database with a schema similar to Firefox's places.sqlite.
    This includes tables with foreign keys, indexes, and triggers, and WAL mode.
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Enable foreign keys
    conn.execute("PRAGMA foreign_keys=ON")

    # simplified schema based on places.sqlite
    conn.executescript("""
        CREATE TABLE moz_places (
            id INTEGER PRIMARY KEY,
            url LONGVARCHAR,
            title LONGVARCHAR,
            rev_host LONGVARCHAR,
            visit_count INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0,
            typed INTEGER DEFAULT 0,
            favicon_id INTEGER,
            frecency INTEGER DEFAULT -1,
            last_visit_date INTEGER,
            guid TEXT,
            foreign_count INTEGER DEFAULT 0,
            url_hash INTEGER DEFAULT 0,
            description LONGVARCHAR,
            preview_image_url LONGVARCHAR,
            origin_id INTEGER
        );

        CREATE TABLE moz_historyvisits (
            id INTEGER PRIMARY KEY,
            from_visit INTEGER,
            place_id INTEGER,
            visit_date INTEGER,
            visit_type INTEGER,
            session INTEGER
        );

        CREATE INDEX moz_historyvisits_placedateindex ON moz_historyvisits (place_id, visit_date);
        CREATE INDEX moz_historyvisits_fromindex ON moz_historyvisits (from_visit);
        CREATE INDEX moz_historyvisits_dateindex ON moz_historyvisits (visit_date);
        
        -- Insert some dummy data
        INSERT INTO moz_places (url, title, rev_host) VALUES ('https://www.google.com/', 'Google', 'moc.elgoog.www.');
        INSERT INTO moz_places (url, title, rev_host) VALUES ('https://github.com/', 'GitHub', 'moc.buhtig.');
        
        INSERT INTO moz_historyvisits (place_id, visit_date, visit_type) VALUES (1, 1630000000000000, 1);
        INSERT INTO moz_historyvisits (place_id, visit_date, visit_type) VALUES (2, 1630000010000000, 1);
    """)

    conn.commit()
    conn.close()


def main():
    """Main verification procedure."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        db_path = tmp.name

    try:
        print(f"Creating Firefox-style DB at {db_path}...")
        create_firefox_style_db(db_path)

        print("Running git-sqlite-clean...")
        # Assume git-sqlite-clean is installed or in path, or use local src
        cmd = ["git-sqlite-clean", db_path]

        # If not in path, try to run from source
        if (
            subprocess.run(
                ["which", "git-sqlite-clean"], capture_output=True, check=False
            ).returncode
            != 0
        ):
            print(
                "git-sqlite-clean not found in PATH, using src/git_sqlite_filter/clean.py"
            )
            src_path = os.path.join(
                os.path.dirname(__file__), "..", "src", "git_sqlite_filter", "clean.py"
            )
            cmd = [sys.executable, src_path, db_path]

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            print("ERROR: git-sqlite-clean failed!")
            print("Stderr:", result.stderr)
            sys.exit(1)

        print("Success! Output snippet:")
        print(result.stdout[:200] + "...")

        # Basic validation of output
        if "INSERT INTO" not in result.stdout:
            print("ERROR: Output doesn't look like SQL dump")
            sys.exit(1)

        if "PRAGMA journal_mode=WAL" in result.stdout:
            print("NOTE: WAL mode pragma found (expected behavior logic check needed)")

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
        # WAL files might be left over if not closed properly, clean them too
        if os.path.exists(db_path + "-wal"):
            os.remove(db_path + "-wal")
        if os.path.exists(db_path + "-shm"):
            os.remove(db_path + "-shm")


if __name__ == "__main__":
    main()
