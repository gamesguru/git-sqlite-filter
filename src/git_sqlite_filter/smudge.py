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

TOOL = "[git-sqlite-smudge]"


def log(msg):
    sys.stderr.write(f"{TOOL} {msg}\n")


def collation_func(s1, s2):
    """Dumb lexicographical sort for unregistered collations."""
    if s1 == s2:
        return 0
    return 1 if s1 > s2 else -1


def filter_sql_stream(stream, debug=False):
    """Filter out internal schema creation and handle transactions."""
    yield "BEGIN TRANSACTION;\n"
    
    # We buffer blocks until we hit a semicolon to handle multiline statements
    buffer = []
    
    for line in stream:
        buffer.append(line)
        
        # If the line ends with a semicolon (ignoring whitespace), it's a statement
        if line.strip().endswith(";"):
            statement = "".join(buffer)
            buffer = []
            
            statement_upper = statement.upper().strip()
            
            # Skip creating sqlite_sequence
            if "CREATE TABLE" in statement_upper and "SQLITE_SEQUENCE" in statement_upper:
                if debug: log("skipping sqlite_sequence creation")
                continue

            # Skip internal metadata insertions
            if ("INSERT INTO" in statement_upper) and ("SQLITE_MASTER" in statement_upper or "SQLITE_STAT" in statement_upper):
                if debug: log(f"skipping internal metadata insert: {statement[:30]}...")
                continue
            
            # Filter transaction commands to prevent nested transactions
            if any(statement_upper.startswith(p) for p in ["BEGIN TRANSACTION", "COMMIT", "ROLLBACK"]):
                if debug: log(f"skipping transaction command: {statement_upper}")
                continue

            yield statement
            
    # Yield anything remaining (shouldn't happen with well-formed CSV)
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
        for _ in range(max_retries):
            self._create_temp_db()
            try:
                if self.debug:
                    log(f"executing SQL script ({len(sql_script)} bytes)...")
                self.conn.executescript(sql_script)
                return True
            except sqlite3.OperationalError as e:
                if self.debug:
                    log(f"caught operational error: {e}")
                if not self._ensure_collation(e):
                    log(f"error: restore failed: {e}")
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
    parser.add_argument("--schema", help="Path to a base schema file to apply before data")
    parser.add_argument("--debug", action="store_true", help="Log debug info to stderr")
    args = parser.parse_args()

    # 1. Prepare SQL script
    sql_lines = []
    if args.schema and os.path.exists(args.schema):
        if args.debug:
            log(f"loading schema from {args.schema}")
        with open(args.schema, "r") as f:
            sql_lines.extend(f.readlines())

    if args.debug:
        log("parsing SQL from stdin")
    
    # We pass the stream to our statement-aware filter
    sql_lines.extend(list(filter_sql_stream(sys.stdin, debug=args.debug)))
    script = "".join(sql_lines)

    # 2. Rebuild Database
    restorer = DatabaseRestorer(debug=args.debug)
    try:
        if restorer.restore(script):
            restorer.stream_to_stdout()
        else:
            sys.exit(1)
    finally:
        restorer.cleanup()


if __name__ == "__main__":
    main()
