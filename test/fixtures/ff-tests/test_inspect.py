from io import StringIO
from unittest.mock import patch

from ffpass import main


def test_inspect_output(clean_profile):
    """
    Verifies that the 'inspect' command runs and produces Expected output
    including warnings for 14-byte IVs.
    """
    # 1. Test 14-byte IV profile (Should Warn, Default = Obscure)
    profile_14 = clean_profile("firefox-14iv")

    with patch("sys.stdout", new=StringIO()) as fake_out:
        with patch("sys.argv", ["ffpass", "inspect", "-d", str(profile_14)]):
            main()

        output = fake_out.getvalue()
        assert "Inspecting Profile:" in output
        assert "WARNING: Non-standard 14-byte IV detected" in output
        assert "REQUIRES Native NSS backend" in output
        assert "KDF OID:" in output
        # Verify obscuration (partial)
        # assert "(Use --reveal-keys to show full)" in output
        # Should contain "..." for omitted middle part
        # assert "..." in output
        # But not "[HIDDEN]" anymore
        assert "[HIDDEN]" not in output



    # 3. Test Legacy Profile (3DES)
    profile_legacy = clean_profile("firefox-70")
    with patch("sys.stdout", new=StringIO()) as fake_out:
        with patch("sys.argv", ["ffpass", "inspect", "-d", str(profile_legacy)]):
            main()
        output = fake_out.getvalue()
        assert "ID: password" in output
