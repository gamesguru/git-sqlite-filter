import logging


def get_native_logins(directory, password) -> "list | None":
    """
    Attempts to decrypt logins using safe, native NSS interactions via ctypes.
    Returns list of logins or None/Empty list on failure.
    """
    try:
        try:
            from ffpass.nss import decrypt_logins_native  # type: ignore
        except ImportError:
            try:
                from nss import decrypt_logins_native  # type: ignore
            except ImportError:
                return None

        return decrypt_logins_native(directory, password)
    except Exception as e:
        logging.debug(f"Native decryption attempt failed: {e}")
        return None
