"""Shared utilities for git-sqlite-filter."""

import os
import re
import signal
import subprocess
import sys

# Handle broken pipes (e.g. | head) without stack trace (Unix only)
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def get_common_args(parser):
    """Add common arguments to the CLI parser."""
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log debug info to stderr (also triggered by GIT_TRACE=1)",
    )
    return parser.parse_args()


def collation_func(s1, s2):
    """Simple collation function for consistent ordering."""
    return 0 if s1 == s2 else (1 if s1 > s2 else -1)


def extract_missing_collation(error_msg):
    """Extract collation name from an SQLite OperationalError message."""
    match = re.search(r"no such collation sequence: (\S+)", str(error_msg))
    if match:
        return match.group(1).strip("'\"")
    return None


def get_superproject_root():
    """Get the root of the superproject if we are in a submodule."""
    if not os.path.exists(".git") or not os.path.isfile(".git"):
        return None

    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-superproject-working-tree"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        ).strip()
        return out if out else None
    except subprocess.CalledProcessError:
        return None


def get_git_config_bool(key, cwd=None):
    """Get a git config boolean value (true/false) handling various formats."""
    try:
        cmd = ["git"]
        if cwd:
            cmd.extend(["-C", cwd])
        cmd.extend(["config", "--type=bool", "--get", key])

        val = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, encoding="utf-8"
        ).strip()
        return val == "true"
    except subprocess.CalledProcessError:
        return False


def log(tool_name, msg):
    """Write a message to stderr with the tool prefix."""
    sys.stderr.write(f"{tool_name} {msg}\n")


def should_skip_submodule(tool_name):
    """Check if we are in a submodule and configured to skip."""
    # Submodule optimization check: Skip smudge if configured to ignore-submodules
    super_root = get_superproject_root()
    if super_root:
        ignored = get_git_config_bool("sqlite-filter.ignore-submodules")
        if not ignored:
            ignored = get_git_config_bool(
                "sqlite-filter.ignore-submodules", cwd=super_root
            )

        if ignored:
            log(tool_name, "skipping submodule scan (configured to ignore)")
            return True

        log(tool_name, "tip: using sqlite filter in submodules can be slow.")
        log(
            tool_name,
            "     run 'git config sqlite-filter.ignore-submodules true' "
            "in the superproject to skip.",
        )
        return False
    return False
