#!/usr/bin/env python3
import os
import re
import sqlite3


def collation_func(s1, s2):
    return 0 if s1 == s2 else (1 if s1 > s2 else -1)


def create_db(path, sql, user_version=None):
    if os.path.exists(path):
        os.remove(path)

    registered_collations = set()

    while True:
        conn = sqlite3.connect(path)
        for c in registered_collations:
            conn.create_collation(c, collation_func)

        if user_version is not None:
            conn.execute(f"PRAGMA user_version = {user_version}")

        try:
            conn.executescript(sql)
            conn.commit()
            conn.close()
            break
        except sqlite3.OperationalError as e:
            msg = str(e)
            match = re.search(r"no such collation sequence: (\S+)", msg)
            if match:
                col_name = match.group(1).strip("'\"")
                if col_name not in registered_collations:
                    print(f"Generator registering missing collation: {col_name}")
                    registered_collations.add(col_name)
                    conn.close()
                    continue
            print(f"Generator error creating {path}: {e}")
            conn.close()
            break

    print(f"Created: {path}")


def main():
    fixture_dir = "test/fixtures"
    os.makedirs(fixture_dir, exist_ok=True)

    # 1. Version 0 DB
    create_db(
        f"{fixture_dir}/version_0.db", "CREATE TABLE t1 (id INTEGER);", user_version=0
    )

    # 2. Huge Version DB
    create_db(
        f"{fixture_dir}/version_huge.db",
        "CREATE TABLE t1 (id INTEGER);",
        user_version=2147483647,
    )

    # 3. Custom Collation DB (Firefox style)
    create_db(
        f"{fixture_dir}/collation_edge.db",
        """
        CREATE TABLE places (
            id INTEGER PRIMARY KEY,
            url TEXT COLLATE UUID,
            title TEXT COLLATE MANIFEST_INDEX
        );
        INSERT INTO places (url, title) VALUES ('https://google.com', 'Search');
    """,
    )

    # 4. Blob / Large Data DB
    create_db(
        f"{fixture_dir}/blobs.db",
        """
        CREATE TABLE assets (id INTEGER, data BLOB);
        INSERT INTO assets VALUES (1, zeroblob(1024 * 100)); -- 100KB blob
    """,
    )

    # 5. FTS5 (Virtual Table)
    create_db(
        f"{fixture_dir}/fts.db",
        """
        CREATE VIRTUAL TABLE docs USING fts5(content);
        INSERT INTO docs VALUES ('The quick brown fox');
        INSERT INTO docs VALUES ('Jumped over the lazy dog');
    """,
    )

    # 6. Generated Columns
    create_db(
        f"{fixture_dir}/generated_cols.db",
        """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            price REAL,
            tax_rate REAL,
            total REAL GENERATED ALWAYS AS (price * (1 + tax_rate)) VIRTUAL
        );
        INSERT INTO items (price, tax_rate) VALUES (10.0, 0.2);
    """,
    )

    # 7. Complex Constraints (Triggers & CHECK)
    create_db(
        f"{fixture_dir}/constraints.db",
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            age INTEGER CHECK(age >= 18),
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE logs (msg TEXT);
        CREATE TRIGGER user_log AFTER INSERT ON users BEGIN
            INSERT INTO logs VALUES ('New user ' || NEW.id);
        END;
        INSERT INTO users (age) VALUES (25);
    """,
    )

    # 8. AUTOINCREMENT Preservation
    create_db(
        f"{fixture_dir}/autoincrement.db",
        """
        CREATE TABLE seq_test (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT);
        INSERT INTO seq_test (val) VALUES ('A'), ('B'), ('C');
        DELETE FROM seq_test WHERE id = 3; -- Current max id is 3, but highest counter in sequence should be 3
    """,
    )

    # 9. Mixed Edge Case (reserved names, weird types)
    create_db(
        f"{fixture_dir}/mixed_edge.db",
        """
        CREATE TABLE "order" (
            "index" INTEGER PRIMARY KEY,
            "values" TEXT,
            "check" TEXT
        );
        INSERT INTO "order" VALUES (1, 'mixed data', 'ok');
    """,
    )


if __name__ == "__main__":
    main()
