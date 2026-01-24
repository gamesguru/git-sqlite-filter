import shutil
from pathlib import Path

import pytest


@pytest.fixture
def clean_profile(tmp_path):
    """
    Copies the requested profile to a temporary directory and returns
    the path to the new copy.
    """

    def _setup(profile_name):
        src = Path("test/fixtures/ff_tests") / profile_name
        dst = tmp_path / profile_name
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return dst

    return _setup
