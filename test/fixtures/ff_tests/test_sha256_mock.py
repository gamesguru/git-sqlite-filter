#!/usr/bin/env python3

import os
import sqlite3
from hashlib import pbkdf2_hmac, sha1, sha256

from Crypto.Cipher import AES  # type: ignore
from pyasn1.codec.der.encoder import encode as der_encode  # type: ignore
from pyasn1.type.univ import Integer, ObjectIdentifier, OctetString, Sequence  # type: ignore

import ffpass  # type: ignore

# Constants from ffpass (redefined here for test generation)
OID_PBES2 = (1, 2, 840, 113_549, 1, 5, 13)
OID_PBKDF2 = (1, 2, 840, 113_549, 1, 5, 12)
OID_AES256_CBC = (2, 16, 840, 1, 101, 3, 4, 1, 42)


def PKCS7pad(b, block_size=8):
    pad_len = (-len(b) - 1) % block_size + 1
    return b + bytes([pad_len] * pad_len)


def build_pbes2_sequence(
    global_salt, master_password, entry_salt, iters, plaintext, hash_method="sha256"
):
    """
    Constructs a DER-encoded PBES2 sequence just like Firefox/ffpass expects.
    """
    # 1. Derive Key
    if hash_method == "sha1":
        enc_pwd = sha1(global_salt + master_password.encode("utf-8")).digest()
    elif hash_method == "sha256":
        enc_pwd = sha256(global_salt + master_password.encode("utf-8")).digest()
    elif hash_method == "plaintext":
        enc_pwd = master_password.encode("utf-8")
    else:
        raise ValueError(f"Unknown hash method: {hash_method}")

    key_len = 32
    k = pbkdf2_hmac("sha256", enc_pwd, entry_salt, iters, dklen=key_len)

    # 2. Encrypt
    iv = os.urandom(16)
    cipher = AES.new(k, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(PKCS7pad(plaintext, block_size=16))

    # 3. Build ASN.1 Structure
    # ... (rest is same)
    # Re-using previous logic structure but just checking it matches

    # Key Derivation Func
    kdf_params = Sequence()
    kdf_params.setComponentByPosition(0, OctetString(entry_salt))
    kdf_params.setComponentByPosition(1, Integer(iters))
    kdf_params.setComponentByPosition(2, Integer(key_len))

    # Add PRF OID (AlgorithmIdentifier)
    # 1.2.840.113549.2.9 = hmacWithSHA256
    alg_id_prf = Sequence()
    alg_id_prf.setComponentByPosition(0, ObjectIdentifier((1, 2, 840, 113_549, 2, 9)))
    # Parameters matches NULL usually or excluded? Let's check spec or assume explicit NULL is safe or omitted.
    # ffpass logic checks len(pbkdf2_params) > 3. So we must add it as 4th element.
    kdf_params.setComponentByPosition(3, alg_id_prf)

    kdf = Sequence()
    kdf.setComponentByPosition(0, ObjectIdentifier(OID_PBKDF2))
    kdf.setComponentByPosition(1, kdf_params)

    # Encryption Scheme
    enc_scheme = Sequence()
    enc_scheme.setComponentByPosition(0, ObjectIdentifier(OID_AES256_CBC))
    enc_scheme.setComponentByPosition(1, OctetString(iv))

    pbes2_seq = Sequence()
    pbes2_seq.setComponentByPosition(0, kdf)
    pbes2_seq.setComponentByPosition(1, enc_scheme)

    alg_id = Sequence()
    alg_id.setComponentByPosition(0, ObjectIdentifier(OID_PBES2))
    alg_id.setComponentByPosition(1, pbes2_seq)

    top = Sequence()
    top.setComponentByPosition(0, alg_id)
    top.setComponentByPosition(1, OctetString(ciphertext))

    return der_encode(top)


def test_plaintext_decryption(tmp_path):
    print("Setting up test_plaintext_decryption...")
    db_path = tmp_path / "key4.db"

    if db_path.exists():
        os.remove(db_path)

    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("CREATE TABLE metadata (id TEXT, item1 BLOB, item2 BLOB)")
    c.execute("CREATE TABLE nssPrivate (a11 BLOB, a102 BLOB)")

    global_salt = os.urandom(20)
    master_password = "test-plaintext"

    entry_salt_check = os.urandom(20)
    # Use PLAINTEXT here!
    item2 = build_pbes2_sequence(
        global_salt,
        master_password,
        entry_salt_check,
        iters=1000,
        plaintext=b"password-check",
        hash_method="plaintext",
    )

    c.execute(
        "INSERT INTO metadata (id, item1, item2) VALUES (?, ?, ?)",
        ("password", global_salt, item2),
    )

    real_master_key = b"B" * 32
    entry_salt_key = os.urandom(20)

    a11 = build_pbes2_sequence(
        global_salt,
        master_password,
        entry_salt_key,
        iters=1000,
        plaintext=real_master_key,
        hash_method="plaintext",
    )
    a102 = b"\x00" * 16

    c.execute("INSERT INTO nssPrivate (a11, a102) VALUES (?, ?)", (a11, a102))

    conn.commit()
    conn.close()

    print(f"Database created at {db_path}")

    print("Attempting to unlock (plaintext)...")
    keys, returned_salt = ffpass.get_all_keys(tmp_path, master_password)

    print(f"Unlocked! Found {len(keys)} keys.")
    assert len(keys) == 1
    assert keys[0] == real_master_key
    assert returned_salt == global_salt
    print("SUCCESS: Master key verified correctly with PLAINTEXT hashing.")


def test_sha256_decryption(tmp_path):
    import logging

    logging.basicConfig(level=logging.DEBUG)
    print("Setting up test_sha256_decryption...")
    db_path = tmp_path / "key4.db"

    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("CREATE TABLE metadata (id TEXT, item1 BLOB, item2 BLOB)")
    c.execute("CREATE TABLE nssPrivate (a11 BLOB, a102 BLOB)")

    global_salt = os.urandom(20)
    master_password = "test-new-profile"

    # 1. Create 'password' metadata entry
    # item1 = global_salt
    # item2 = encrypted "password-check" (padded to "password-check\x02\x02")

    entry_salt_check = os.urandom(20)
    # Use SHA256 here!
    item2 = build_pbes2_sequence(
        global_salt,
        master_password,
        entry_salt_check,
        iters=1000,
        plaintext=b"password-check",
        hash_method="sha256",
    )

    c.execute(
        "INSERT INTO metadata (id, item1, item2) VALUES (?, ?, ?)",
        ("password", global_salt, item2),
    )

    # 2. Create a 'nssPrivate' master key entry
    # The actual master key (random 32 bytes)
    real_master_key = b"A" * 32
    entry_salt_key = os.urandom(20)

    # Encrypt the master key using the same derived key logic
    a11 = build_pbes2_sequence(
        global_salt,
        master_password,
        entry_salt_key,
        iters=1000,
        plaintext=real_master_key,
        hash_method="sha256",
    )
    # a102 is just the ID/Name of the key (usually looks like magic bytes)
    a102 = b"\x00" * 16

    c.execute("INSERT INTO nssPrivate (a11, a102) VALUES (?, ?)", (a11, a102))

    conn.commit()
    conn.close()

    print(f"Database created at {db_path}")

    # Now run ffpass logic
    print("Attempting to unlock...")
    keys, returned_salt = ffpass.get_all_keys(tmp_path, master_password)

    print(f"Unlocked! Found {len(keys)} keys.")
    assert len(keys) == 1
    assert keys[0] == real_master_key
    assert returned_salt == global_salt
    print("SUCCESS: Master key verified correctly with SHA256 hashing.")
