import os
import subprocess
import pytest
import sqlite3
import io
import sys
from git_sqlite_filter.clean import DatabaseDumper
from git_sqlite_filter.smudge import DatabaseRestorer

FIXTURE_DIR = "test/fixtures"
TMP_DIR = ".tmp/test_runs"

@pytest.fixture(scope="session", autouse=True)
def setup_fixtures():
    os.makedirs(TMP_DIR, exist_ok=True)
    subprocess.run([sys.executable, "test/generate_test_dbs.py"], check=True)

def get_fixtures():
    return [os.path.join(FIXTURE_DIR, f) for f in os.listdir(FIXTURE_DIR) if f.endswith(".db")]

@pytest.mark.parametrize("db_path", get_fixtures())
def test_semantic_parity(db_path):
    db_name = os.path.basename(db_path)
    
    # Step A: Clean original DB -> SQL Dump A
    args_a = type('Args', (), {'float_precision': 5, 'schema_only': False, 'data_only': False, 'debug': False})()
    dumper_a = DatabaseDumper(db_path, args_a)
    
    out_a = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = out_a
    try:
        dumper_a.dump()
    finally:
        sys.stdout = old_stdout
    
    dump_a = out_a.getvalue()
    assert dump_a, f"Dump A for {db_name} is empty"

    # Step B: Smudge SQL Dump A -> Rebuilt DB
    restorer = DatabaseRestorer(debug=False)
    # The restorer uses filter_sql_stream in main(), but here we can just pass the dump
    # However, the smudge filter typically expects the output of filter_sql_stream.
    # Let's replicate the main smudge logic:
    from git_sqlite_filter.smudge import filter_sql_stream
    filtered_script = "".join(list(filter_sql_stream(io.StringIO(dump_a), debug=False)))
    
    assert restorer.restore(filtered_script), f"Restoration failed for {db_name}"
    rebuilt_db_path = restorer.tmp_path

    try:
        # Step C: Clean Rebuilt DB -> SQL Dump B
        dumper_b = DatabaseDumper(rebuilt_db_path, args_a)
        out_b = io.StringIO()
        sys.stdout = out_b
        try:
            dumper_b.dump()
        finally:
            sys.stdout = old_stdout
            
        dump_b = out_b.getvalue()
        
        # Step D: Compare
        assert dump_a == dump_b, f"Semantic mismatch for {db_name}"
    finally:
        restorer.cleanup()

def test_binary_fallback():
    binary_db = os.path.join(TMP_DIR, "binary_only.db")
    content = "raw binary content\n"
    with open(binary_db, "w") as f:
        f.write(content)
        
    args = type('Args', (), {'float_precision': 5, 'schema_only': False, 'debug': False})()
    dumper = DatabaseDumper(binary_db, args)
    
    out = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = out
    try:
        # We need to simulate the main check if DatabaseDumper doesn't handle non-sqlite files internally
        # Actually DatabaseDumper.run expects a valid sqlite file. 
        # The main() in clean.py handles the fallback.
        # Let's test by calling the script as a subprocess to be sure we hit the main logic.
        cmd = [sys.executable, "src/git_sqlite_filter/clean.py", binary_db]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert content in result.stdout
    finally:
        sys.stdout = old_stdout
