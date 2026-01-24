"""Verification tests running against real binaries or modules."""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

# Constants
TEST_DIR = "test/fixtures"


def run_clean(db_path):
    """Run the clean filter on a database file."""
    # Always use the module to test the current code, not installed binary
    cmd = [sys.executable, "-m", "git_sqlite_filter.clean", str(db_path)]
    env = os.environ.copy()

    # Critical: Ensure src/ is in PYTHONPATH
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env.get('PYTHONPATH', '')}"

    # Run git-sqlite-clean
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        return None, result.stderr
    return result.stdout, None


def run_smudge(sql_input):
    """Run the smudge filter on SQL input."""
    # Always use the module to test the current code, not installed binary
    cmd = [sys.executable, "-m", "git_sqlite_filter.smudge"]
    env = os.environ.copy()
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env.get('PYTHONPATH', '')}"

    # Encode input to bytes as smudge expects binary input stream
    if isinstance(sql_input, str):
        sql_input = sql_input.encode("utf-8")

    result = subprocess.run(
        cmd,
        input=sql_input,
        capture_output=True,
        text=None,  # Capture binary output
        env=env,
        check=False,
    )
    if result.returncode != 0:
        print(f"Smudge error: {result.stderr.decode('utf-8', errors='replace')}")
        return None, result.stderr.decode("utf-8", errors="replace")
    return result.stdout, None


def run_sqlite_dump(db_path):
    """Run sqlite3 .dump on the database file."""
    cmd = ["sqlite3", db_path, ".dump"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None, result.stderr
    return result.stdout, None


def normalize(sql):
    """Normalize SQL for rough comparison (ignoring comments/whitespace/sorting)."""
    if not sql:
        return []
    lines = [
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.startswith("--")
    ]
    # Filter out FTS shadow tables (internal representation matches are not guaranteed)
    lines = [
        line
        for line in lines
        if not re.search(r'INSERT INTO "?.*_(data|idx|content|docsize|config)"?', line)
    ]
    lines.sort()
    return lines


@pytest.mark.parametrize(
    "db_file", glob.glob(os.path.join(TEST_DIR, "**", "*.db"), recursive=True)
)
def test_full_round_trip(db_file):
    """
    Verify correctness by full round-trip:
    Original DB -> clean -> SQL -> smudge -> New DB
    Then compare:
    Original DB .dump == New DB .dump
    """

    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 binary not found in PATH")

    print(f"Verifying {db_file}...")

    # 1. Clean
    clean_out, clean_err = run_clean(db_file)
    assert clean_out is not None, f"Clean failed: {clean_err}"
    assert clean_out.strip(), "Clean produced empty output"

    # 2. Smudge
    smudge_out_bytes, smudge_err = run_smudge(clean_out)
    assert smudge_out_bytes is not None, f"Smudge failed: {smudge_err}"

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        tmp_db.write(smudge_out_bytes)
        tmp_db_path = tmp_db.name

    try:
        _verify_round_trip_equality(db_file, tmp_db_path)
    finally:
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)


def _verify_round_trip_equality(original_db, restored_db):
    """Helpers to dump and compare two databases."""
    # 3. Dump both
    dump_orig, err_orig = run_sqlite_dump(original_db)
    assert dump_orig is not None, f"Dump original failed: {err_orig}"

    dump_new, err_new = run_sqlite_dump(restored_db)
    assert dump_new is not None, f"Dump restored failed: {err_new}"

    # 4. Compare
    lines_orig = normalize(dump_orig)
    lines_new = normalize(dump_new)

    diff_count = abs(len(lines_orig) - len(lines_new))

    if diff_count > 0:
        set_orig = set(lines_orig)
        set_new = set(lines_new)
        unique_orig = list(set_orig - set_new)[:5]
        unique_new = list(set_new - set_orig)[:5]
        print(f"\nUnique to original: {unique_orig}")
        print(f"Unique to restored: {unique_new}")

    assert (
        lines_orig == lines_new
    ), f"Round-trip content mismatch! Diff count: {diff_count}"
