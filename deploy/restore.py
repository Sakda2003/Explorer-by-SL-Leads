#!/usr/bin/env python3
"""Decrypt and verify a LeadLens backup, optionally restoring it in place.

Verification is separated from restoring on purpose. `--check` proves a backup is intact and
openable without touching the live database, which is what makes it safe to run the drill
regularly. A backup you have never restored is a hypothesis, not a backup.

Usage:
    LEADLENS_BACKUP_PASSPHRASE=... python restore.py --check  backup.db.gz.age
    LEADLENS_BACKUP_PASSPHRASE=... python restore.py --out /tmp/recovered.db backup.db.gz.age
    LEADLENS_BACKUP_PASSPHRASE=... python restore.py --in-place /data/leadlens.db backup.db.gz.age
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup import MAGIC, NONCE_LEN, SALT_LEN, derive_key  # noqa: E402


def decrypt(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not a LeadLens backup (bad magic header)")
    off = len(MAGIC)
    salt = blob[off : off + SALT_LEN]
    nonce = blob[off + SALT_LEN : off + SALT_LEN + NONCE_LEN]
    ciphertext = blob[off + SALT_LEN + NONCE_LEN :]
    key = derive_key(passphrase, salt)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, MAGIC)
    except InvalidTag as exc:
        # GCM cannot tell these apart, and guessing would be misleading.
        raise ValueError(
            "decryption failed: wrong passphrase, or the file is corrupt or truncated"
        ) from exc


def recover(path: Path, passphrase: str) -> bytes:
    return gzip.decompress(decrypt(path.read_bytes(), passphrase))


def describe(db_path: Path) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check: {integrity}")
        counts = []
        for table in ("lead_events", "daily_ad_performance", "forecasts", "raw_uploads"):
            try:
                n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                counts.append(f"{table}={n}")
            except sqlite3.Error:
                counts.append(f"{table}=absent")
        return "integrity=ok  " + "  ".join(counts)
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", type=Path)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify only; touches nothing")
    mode.add_argument("--out", type=Path, help="write the recovered database here")
    mode.add_argument("--in-place", type=Path, help="replace this live database")
    args = ap.parse_args()

    passphrase = os.environ.get("LEADLENS_BACKUP_PASSPHRASE", "")
    if not passphrase:
        print("LEADLENS_BACKUP_PASSPHRASE is not set", file=sys.stderr)
        return 1
    if not args.backup.exists():
        print(f"no such backup: {args.backup}", file=sys.stderr)
        return 1

    try:
        raw = recover(args.backup, passphrase)
    except ValueError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "recovered.db"
        staged.write_bytes(raw)
        try:
            summary = describe(staged)
        except Exception as exc:
            print(f"FAILED: recovered file is not a usable database: {exc}", file=sys.stderr)
            return 1

        print(f"{args.backup.name}: {summary}")

        if args.check:
            return 0

        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged, args.out)
            print(f"written to {args.out}")
            return 0

        target = args.in_place
        # Keep the database we are about to overwrite. If this backup turns out to be from the
        # wrong day, that decision needs to be reversible.
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            aside = target.with_name(f"{target.name}.replaced-{stamp}")
            shutil.copy2(target, aside)
            print(f"previous database preserved at {aside}")
        # WAL sidecars describe the old database; leaving them next to a restored file is how
        # you get a mismatched, unopenable pair.
        for sidecar in (f"{target}-wal", f"{target}-shm"):
            Path(sidecar).unlink(missing_ok=True)
        shutil.copy2(staged, target)
        print(f"restored to {target}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
