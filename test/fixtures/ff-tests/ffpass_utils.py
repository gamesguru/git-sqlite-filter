from pathlib import Path
import logging

def get_native_logins(directory, password) -> "list | None":
    """
    Attempts to decrypt logins using safe, native NSS interactions via ctypes.
    Returns list of logins or None/Empty list on failure.
    """
    try:
        # Import explicitly to handle potential path issues
        try:
            from ffpass.nss import decrypt_logins_native
        except ImportError:
            try:
                from nss import decrypt_logins_native
            except ImportError:
                import sys

                root_dir = str(Path(__file__).resolve().parent.parent)
                if root_dir not in sys.path:
                    sys.path.append(root_dir)
                from ffpass.nss import decrypt_logins_native

        return decrypt_logins_native(directory, password)
    except Exception as e:
        logging.debug(f"Native decryption attempt failed: {e}")
        return None
