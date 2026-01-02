import sqlite3
import os
import subprocess
import sys

db_path = "test/fixtures/mixed_edge.db"
os.makedirs("test/fixtures", exist_ok=True)

# Register custom collation for generation
def uuid_collation(s1, s2):
    return (s1 > s2) - (s1 < s2)

conn = sqlite3.connect(db_path)
conn.create_collation("UUID", uuid_collation)
conn.executescript("""
    CREATE TABLE users (id INTEGER PRIMARY KEY, uid TEXT COLLATE UUID);
    INSERT INTO users VALUES (1, 'z'), (2, 'a');
    CREATE VIRTUAL TABLE docs USING fts5(content);
    INSERT INTO docs VALUES ('The quick brown fox');
""")
conn.close()

print("Generated mixed_edge.db")

# Now run clean
clean_path = "src/git_sqlite_filter/clean.py"
smudge_path = "src/git_sqlite_filter/smudge.py"

clean_cmd = ["python3", clean_path, db_path]
res_clean = subprocess.run(clean_cmd, capture_output=True, text=True)

if res_clean.returncode != 0:
    print("Clean failed")
    print(res_clean.stderr)
    sys.exit(1)

dump_sql = res_clean.stdout
print("Dump generated successfully")

# Now run smudge
res_smudge = subprocess.run(["python3", smudge_path], input=dump_sql, capture_output=True, text=True)

print("--- SMUDGE STDOUT ---")
# print(res_smudge.stdout) # (Binary DB)
print("--- SMUDGE STDERR ---")
print(res_smudge.stderr)

if "no such table: docs" in res_smudge.stderr:
    print("FAILURE REPRODUCED: no such table: docs")
else:
    print("Failure NOT reproduced")
