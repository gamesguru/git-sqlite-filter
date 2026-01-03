import sys

import pytest

from .ffpass_utils import get_native_logins


def test_native_old_profile(clean_profile):
    # Firefox 70 profile (legacy)
    profile = clean_profile("firefox-70")
    # Password 'test' (from test_run.py)
    logins = get_native_logins(str(profile), "test")

    assert len(logins) > 0

    # Expected based on test_run.py: ["http://www.stealmylogin.com", "test", "test"]
    found = False
    for login in logins:
        if login["hostname"] == "http://www.stealmylogin.com":
            found = True
            assert login["username"] == "test"
            assert login["password"] == "test"
            break
    assert found, "Expected login not found in native export"


def test_native_new_profile(clean_profile):
    # Firefox 14-byte IV profile (from user)
    profile = clean_profile("firefox-14iv")
    # Password 'pass'
    logins = get_native_logins(str(profile), "pass")

    assert len(logins) > 0

    # Expected: ["https://google.com", "test", "pass"]
    found = False
    for login in logins:
        if login["hostname"] == "https://google.com":
            found = True
            assert login["username"] == "test"
            assert login["password"] == "pass"
            break
    assert found, "Expected login not found in native export"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
