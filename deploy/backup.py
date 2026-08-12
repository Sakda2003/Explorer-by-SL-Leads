#!/usr/bin/env python3
"""Produce an encrypted, consistent snapshot of the LeadLens database.

Runs inside the app image, which already has everything needed -- `cryptography` arrives with
pyjwt[crypto], so backups add no new dependency to install or keep patched.

Two things here are non-obvious and load-bearing:

1. The snapshot uses SQLite's online backup API, never a file copy. The database runs in WAL
   mode, so at any instant the newest committed pages may live in `leadlens.db-wal` rather
   than `leadlens.db`. Copying the main file alone can therefore yield a database that is
   stale, or torn mid-transaction and unopenable. The backup API walks a consistent snapshot
   while writers continue.

2. The file is encrypted with AES-256-GCM, which is *authenticated*: a corrupted or tampered
   backup fails to decrypt rather than silently restoring damaged customer records. The key is
   derived per-backup with scrypt from a passphrase, and the random salt is stored in the
   header, so the same passphrase never produces the same key twice.

Usage (normally invoked by deploy/backup.sh, not by hand):
    LEADLENS_BACKUP_PASSPHRASE=... python backup.py /data/leadlens.db /backups/out.db.gz.age
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"LLBAK\x00\x01\x00"  # 8 bytes: format marker + version
SALT_LEN = 16
NONCE_LEN = 12
# scrypt cost. n=2**15 keeps derivation around a fifth of a second on a small VPS, which is
# irrelevant once a night but expensive enough to make a stolen backup unpleasant to attack.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )


def snapshot(source: Path, target: Path) -> None:
    """Consistent copy of a live WAL database via the online backup API."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            # Verify what we just wrote actually opens and is structurally sound. Catching a
            # corrupt backup now is the whole point; discovering it during a restore is not.
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"snapshot failed integrity_check: {result}")
        finally:
            dst.close()
    finally:
        src.close()


def encrypt(plaintext: bytes, passphrase: str) -> bytes:
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    key = derive_key(passphrase, salt)
    # The header is passed as associated data, so tampering with it also fails authentication.
    header = MAGIC + salt + nonce
    return header + AESGCM(key).encrypt(nonce, plaintext, MAGIC)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source, target = Path(sys.argv[1]), Path(sys.argv[2])

    passphrase = os.environ.get("LEADLENS_BACKUP_PASSPHRASE", "")
    if len(passphrase) < 16:
        print(
            "LEADLENS_BACKUP_PASSPHRASE missing or shorter than 16 characters.\n"
            "This is the only thing standing between a leaked backup and 2,707 customer "
            "records. Generate a long random one and store it in a password manager.",
            file=sys.stderr,
        )
        return 1

    if not source.exists():
        print(f"no database at {source}", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snapshot.db"
        snapshot(source, snap)
        raw = snap.read_bytes()

    compressed = gzip.compress(raw, compresslevel=6)
    blob = encrypt(compressed, passphrase)

    # Write to a sibling then rename: a cron job killed mid-write must not leave a truncated
    # file sitting where a restore might later pick it up as if it were whole.
    staging = target.with_suffix(target.suffix + ".partial")
    staging.write_bytes(blob)
    os.replace(staging, target)

    print(
        f"{target.name}  "
        f"db={len(raw) / 1_048_576:.1f}MB  "
        f"gz={len(compressed) / 1_048_576:.1f}MB  "
        f"enc={len(blob) / 1_048_576:.1f}MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
