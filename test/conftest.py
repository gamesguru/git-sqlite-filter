"""Pytest configuration and shared fixtures."""

import sys
from pathlib import Path

# Add src to sys.path so tests can import git_sqlite_filter
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))
