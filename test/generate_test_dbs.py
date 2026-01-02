#!/usr/bin/env python3
import sqlite3
import os

def create_db(path, sql, user_version=None):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    if user_version is not None:
        conn.execute(f"PRAGMA user_version = {user_version}")
    conn.executescript(sql)
    conn.commit()
    conn.close()
    print(f"Created: {path}")

def main():
    os.makedirs("test", exist_ok=True)

    # 1. Version 0 DB
    create_db("test/version_0.db", "CREATE TABLE t1 (id INTEGER);", user_version=0)

    # 2. Huge Version DB
    create_db("test/version_huge.db", "CREATE TABLE t1 (id INTEGER);", user_version=2147483647)

    # 3. Custom Collation DB (Firefox style)
    # Note: Opening this in standard tools will fail, which is exactly what we want to test
    create_db("test/collation_edge.db", """
        CREATE TABLE places (
            id INTEGER PRIMARY KEY,
            url TEXT COLLATE UUID,
            title TEXT COLLATE MANIFEST_INDEX
        );
        INSERT INTO places (url, title) VALUES ('https://google.com', 'Search');
    """)

    # 4. Blob / Large Data DB
    create_db("test/blobs.db", """
        CREATE TABLE assets (id INTEGER, data BLOB);
        INSERT INTO assets VALUES (1, zeroblob(1024 * 100)); -- 100KB blob
    """)

    print("\nTo simulate a LOCKED database for testing clean.py:")
    print("Run: sqlite3 test/version_0.db 'BEGIN EXCLUSIVE; SELECT count(*) FROM t1; .timer on; .sleep 10000'")

if __name__ == "__main__":
    main()
