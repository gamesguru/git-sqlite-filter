import io
import os
import sqlite3
import subprocess

from git_sqlite_filter.clean import main as clean_main


def test_debug_mode_and_without_rowid(tmp_path):
    """
    Test clean with --debug enabled (hits log lines)
    and a WITHOUT ROWID table (hits no-rowid fallback).
    """
    db_path = tmp_path / "extensive.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE norowid (id INT PRIMARY KEY, val TEXT) WITHOUT ROWID")
    conn.execute("INSERT INTO norowid VALUES (1, 'a')")
    conn.execute("CREATE TABLE standard (id INT)")
    conn.execute("INSERT INTO standard VALUES (2)")
    conn.commit()
    conn.close()

    output_bytes = io.BytesIO()

    # We can't avoid sys.argv/stdout capture mocking easily without refactoring main(),
    # so we keep that, but we use real file paths.
    from unittest import mock

    with mock.patch(
        "sys.argv", ["git-sqlite-clean", "--debug", str(db_path)]
    ), mock.patch("sys.stdout") as mock_stdout, mock.patch(
        "sys.stderr", new=io.StringIO()
    ) as mock_stderr:

        mock_stdout.write = lambda x: output_bytes.write(
            x.encode("utf-8") if isinstance(x, str) else x
        )
        mock_stdout.buffer = output_bytes

        clean_main()

        log_output = mock_stderr.getvalue()
        assert "starting semantic clean" in log_output
        assert "discovered" in log_output

    sql = output_bytes.getvalue().decode("utf-8")
    assert 'INSERT INTO "norowid"' in sql
    assert 'INSERT INTO "standard"' in sql


def test_submodule_skip_logic(tmp_path):
    """Test submodule skip configuration checks using real git commands."""

    # Setup: Create a superproject and a submodule
    super_dir = tmp_path / "super"
    super_dir.mkdir()

    # Init superproject
    subprocess.check_call(["git", "init"], cwd=super_dir)
    subprocess.check_call(
        ["git", "config", "user.email", "you@example.com"], cwd=super_dir
    )
    subprocess.check_call(["git", "config", "user.name", "Your Name"], cwd=super_dir)

    # Enable the skip config in superproject
    subprocess.check_call(
        ["git", "config", "sqlite-filter.ignore-submodules", "true"], cwd=super_dir
    )

    # Create a separate repo to serve as the upstream for the submodule
    upstream_dir = tmp_path / "upstream"
    upstream_dir.mkdir()
    subprocess.check_call(["git", "init"], cwd=upstream_dir)
    subprocess.check_call(
        ["git", "config", "user.email", "you@example.com"], cwd=upstream_dir
    )
    subprocess.check_call(["git", "config", "user.name", "Your Name"], cwd=upstream_dir)
    (upstream_dir / "README").write_text("upstream")
    subprocess.check_call(["git", "add", "README"], cwd=upstream_dir)
    subprocess.check_call(["git", "commit", "-m", "init upstream"], cwd=upstream_dir)

    # Add submodule to superproject
    subprocess.check_call(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(upstream_dir),
            "sub",
        ],
        cwd=super_dir,
    )
    subprocess.check_call(["git", "commit", "-m", "add submodule"], cwd=super_dir)

    # Now we have a real submodule at super_dir/sub
    sub_dir = super_dir / "sub"
    db_path = sub_dir / "test.db"

    # Create the DB in the submodule
    with open(db_path, "wb") as f:
        f.write(b"SQLite format 3\0")

    # Change CWD to submodule directory (real chdir)
    old_cwd = os.getcwd()
    os.chdir(sub_dir)
    try:
        from unittest import mock

        output_bytes = io.BytesIO()

        # We mock argv/stdout/stderr but use REAL git/subprocess logic in clean.py
        with mock.patch(
            "sys.argv", ["git-sqlite-clean", "--debug", str(db_path)]
        ), mock.patch("sys.stdout") as mock_stdout, mock.patch(
            "sys.stderr", new=io.StringIO()
        ) as mock_stderr:

            mock_stdout.buffer = output_bytes

            clean_main()

            log = mock_stderr.getvalue()
            # Verify it detected it's a submodule and the config says skip
            assert "skipping submodule" in log

    finally:
        os.chdir(old_cwd)
