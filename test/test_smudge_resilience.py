"""Tests for smudge filter resilience against bad input."""

import os
import sqlite3
import unittest
from io import StringIO
from unittest.mock import patch

from git_sqlite_filter.smudge import DatabaseRestorer


class TestSmudgeResilience(unittest.TestCase):
    """Test harness for resilience scenarios."""

    def test_missing_table_warning(self):
        """
        Verify that inserts into missing tables log a warning but do not abort the restoration.
        """
        # SQL with a valid table and an invalid table insert
        sql_script = [
            "PRAGMA foreign_keys=OFF;\n",
            "BEGIN TRANSACTION;\n",
            "CREATE TABLE valid_table (id INTEGER PRIMARY KEY, value TEXT);\n",
            "INSERT INTO valid_table VALUES (1, 'foo');\n",
            # This table does not exist, should trigger warning
            "INSERT INTO missing_table VALUES (1, 'bar');\n",
            "COMMIT;\n",
        ]

        with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
            restorer = DatabaseRestorer(debug=True)
            # Should return True (success) despite the error
            success = restorer.restore(sql_script)

            self.assertTrue(
                success, "Restore should succeed even with missing table errors"
            )

            # Verify the database state
            self.assertIsNotNone(restorer.conn)
            # The original connection is closed, but the file exists at restorer.tmp_path
            self.assertTrue(os.path.exists(restorer.tmp_path))

            with sqlite3.connect(restorer.tmp_path) as check_conn:
                cursor = check_conn.cursor()

                # Valid data should exist
                cursor.execute("SELECT * FROM valid_table")
                rows = cursor.fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0], (1, "foo"))

            # Verify warning was logged
            log_output = mock_stderr.getvalue()
            # Relaxed assertion to handle sqlite version differences (main.table vs table)
            self.assertIn("warning: ignoring error: no such table:", log_output)
            self.assertIn("missing_table", log_output)

    def test_collation_retry_limit(self):
        """
        Verify that we can handle a reasonable number of missing collations.
        The user asked: "why try 100 times? isn't 2 enough?"
        Answer: Each pass only discovers ONE missing collation.
        If a DB uses 10 different custom collations, we need 10 retries.
        """
        # Create a script generating many collation errors
        # We'll use a custom collation function for the test

        # This script needs N passes to discover N collations
        collations = [f"COLL_{i}" for i in range(5)]
        sql_script = ["PRAGMA foreign_keys=OFF;\n", "BEGIN TRANSACTION;\n"]
        for col in collations:
            sql_script.append(f"CREATE TABLE t_{col} (x TEXT COLLATE {col});\n")
            sql_script.append(
                f"SELECT * FROM t_{col} ORDER BY x;\n"
            )  # Trigger collation usage
        sql_script.append("COMMIT;\n")

        with patch("sys.stderr", new_callable=StringIO) as _:
            restorer = DatabaseRestorer(debug=True)
            success = restorer.restore(sql_script)

            self.assertTrue(success)
            self.assertEqual(len(restorer.registered_collations), 5)


if __name__ == "__main__":
    unittest.main()
