#!/usr/bin/env python3

import sys
import subprocess

MASTER_PASSWORD = "test"
HEADER = "url,username,password"
IMPORT_CREDENTIAL = "https://www.example.com,foo,bar"
EXPECTED_EXPORT_OUTPUT = [HEADER, "http://www.stealmylogin.com,test,test"]
EXPECTED_IMPORT_OUTPUT = EXPECTED_EXPORT_OUTPUT + [IMPORT_CREDENTIAL]


def run_ffpass_cmd(mode, path):
    command = [sys.executable, "-c", "from ffpass import main; main()", mode, "--debug", "--dir", str(path)]

    if mode == "import":
        ffpass_input = "\n".join([HEADER, IMPORT_CREDENTIAL])
    else:
        # Pass password via stdin to avoid interactive prompt hang and verify pipe support
        ffpass_input = MASTER_PASSWORD

    return subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, input=ffpass_input, encoding="utf-8"
    )


def stdout_splitter(input_text):
    return [x for x in input_text.splitlines()]


def test_legacy_firefox_export(clean_profile):
    r = run_ffpass_cmd("export", clean_profile("firefox-70"))
    r.check_returncode()
    actual_export_output = stdout_splitter(r.stdout)
    assert actual_export_output == EXPECTED_EXPORT_OUTPUT


def test_firefox_export(clean_profile):
    r = run_ffpass_cmd("export", clean_profile("firefox-84"))
    r.check_returncode()
    assert stdout_splitter(r.stdout) == EXPECTED_EXPORT_OUTPUT


def test_firefox_aes_export(clean_profile):
    # This uses your new AES-encrypted profile
    profile_path = clean_profile("firefox-146-aes")
    r = run_ffpass_cmd("export", profile_path)
    r.check_returncode()
    assert stdout_splitter(r.stdout) == EXPECTED_EXPORT_OUTPUT


def test_legacy_firefox(clean_profile):
    profile_path = clean_profile("firefox-70")

    # modifies the temp file, not the original
    r = run_ffpass_cmd("import", profile_path)
    r.check_returncode()

    r = run_ffpass_cmd("export", profile_path)
    r.check_returncode()
    assert stdout_splitter(r.stdout) == EXPECTED_IMPORT_OUTPUT


def test_firefox(clean_profile):
    profile_path = clean_profile("firefox-84")

    r = run_ffpass_cmd("import", profile_path)
    r.check_returncode()

    r = run_ffpass_cmd("export", profile_path)
    r.check_returncode()
    assert stdout_splitter(r.stdout) == EXPECTED_IMPORT_OUTPUT


def test_firefox_aes(clean_profile):
    profile_path = clean_profile("firefox-146-aes")

    r = run_ffpass_cmd("import", profile_path)
    r.check_returncode()

    r = run_ffpass_cmd("export", profile_path)
    r.check_returncode()
    assert stdout_splitter(r.stdout) == EXPECTED_IMPORT_OUTPUT
