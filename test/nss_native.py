"""Native bindings for NSS (Network Security Services) for testing credentials."""

import base64
import ctypes
import json
import os
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_char_p,
    c_int,
    c_ubyte,
    c_uint,
    c_void_p,
    cast,
)
from pathlib import Path


# --- NSS Structures ---
class SECItem(Structure):
    """Represents a SECItem structure in NSS."""

    # pylint: disable=too-few-public-methods

    _fields_ = [
        ("type", c_uint),
        ("data", POINTER(c_ubyte)),
        ("len", c_uint),
    ]

    def __init__(self):
        """Initialize with default values."""
        super().__init__()
        self.type = 0
        self.data = None
        self.len = 0


# --- Load Library ---
def load_nss():
    """Attempt to load the libnss3 shared library."""
    paths = [
        "/usr/lib/x86_64-linux-gnu/libnss3.so",
        "/usr/lib/libnss3.so",
        "/usr/lib64/libnss3.so",
        "libnss3.so",
    ]
    lib = None
    for p in paths:
        try:
            lib = ctypes.CDLL(p)
            break
        except OSError:
            pass
    return lib


def decrypt_sdr(lib, b64_data):
    """Decrypt base64 data using PK11SDR_Decrypt."""
    if not b64_data:
        return None

    try:
        raw_data = base64.b64decode(b64_data)
    except (TypeError, ValueError):
        return None

    # Prepare SECItem input
    item_in = SECItem()
    item_in.type = 0  # SECItemTypeBuffer
    item_in.len = len(raw_data)

    # Create byte buffer matching length
    buf = (c_ubyte * len(raw_data)).from_buffer_copy(raw_data)
    item_in.data = cast(buf, POINTER(c_ubyte))

    # Output item
    item_out = SECItem()

    ret = lib.PK11SDR_Decrypt(byref(item_in), byref(item_out), None)

    if ret == 0:
        # Success
        content = ctypes.string_at(item_out.data, item_out.len)
        # Free memory allocated by NSS
        lib.SECITEM_FreeItem(
            byref(item_out), 0
        )  # 0 = freeItem (don't free structure itself if not allocated)
        return content.decode("utf-8", errors="replace")

    return None


def get_native_logins(directory: Path, password: str) -> list:
    """
    Uses libnss3 to authenticate and decrypt logins.json.
    Returns a list of dicts: {'hostname': ..., 'username': ..., 'password': ...}
    Returns empty list on failure.
    """

    # pylint: disable=too-many-return-statements

    # Accept Path or str
    profile_path = str(directory)

    lib = load_nss()
    if not lib:
        # If we can't find libnss3, we return empty list or None per previous behavior?
        # Previous helper printed warning and returned None.
        print("WARNING: Could not load libnss3.so. Native validation skipped.")
        return []

    # Function Signatures
    lib.NSS_Init.argtypes = [c_char_p]
    lib.NSS_Init.restype = c_int

    lib.PK11_GetInternalKeySlot.restype = c_void_p

    lib.PK11_CheckUserPassword.argtypes = [c_void_p, c_char_p]
    lib.PK11_CheckUserPassword.restype = c_int

    lib.NSS_Shutdown.restype = c_int

    lib.PK11SDR_Decrypt.argtypes = [POINTER(SECItem), POINTER(SECItem), c_void_p]
    lib.PK11SDR_Decrypt.restype = c_int

    lib.SECITEM_FreeItem.argtypes = [POINTER(SECItem), c_int]
    lib.SECITEM_FreeItem.restype = None

    # Initialize NSS
    db_dir = os.path.abspath(profile_path)
    if db_dir.endswith("/key4.db") or db_dir.endswith("/logins.json"):
        db_dir = os.path.dirname(db_dir)

    config_dir = f"sql:{db_dir}".encode("utf-8")

    # We must try to init. If already inited by process?
    # Python process usually distinct.
    ret = lib.NSS_Init(config_dir)
    if ret != 0:
        print(f"WARNING: NSS_Init failed (Ret: {ret}).")
        return []

    try:
        # Authenticate
        slot = lib.PK11_GetInternalKeySlot()
        if not slot:
            raise RuntimeError("Could not get internal key slot")

        res = lib.PK11_CheckUserPassword(slot, password.encode("utf-8"))
        if res != 0:
            print("WARNING: Invalid Password (NSS rejected)")
            return []

        # Read logins.json
        logins_path = os.path.join(db_dir, "logins.json")
        if not os.path.exists(logins_path):
            return []  # No file, empty list

        with open(logins_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "logins" not in data:
            return []

        results = _parse_logins_data(lib, data["logins"])
        return results

    except (RuntimeError, OSError, TypeError, ValueError) as e:
        print(f"Native decryption loop failed: {e}")
        return []
    finally:
        lib.NSS_Shutdown()


def _parse_logins_data(lib, logins_list):
    """Decrypt the list of login entries."""
    results = []
    for login in logins_list:
        hostname = login.get("hostname", "")
        enc_user = login.get("encryptedUsername")
        enc_pass = login.get("encryptedPassword")

        dec_user = decrypt_sdr(lib, enc_user)
        dec_pass = decrypt_sdr(lib, enc_pass)

        if dec_user is None:
            dec_user = "(error)"
        if dec_pass is None:
            dec_pass = "(error)"

        results.append(
            {"hostname": hostname, "username": dec_user, "password": dec_pass}
        )
    return results
