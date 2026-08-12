"""Backup and restore tests.

The failure this suite exists to prevent is a backup that appears to work for months and turns
out to be unrestorable on the one day it matters. So these tests exercise the real round trip
against a real SQLite database, including a WAL database with uncommitted-to-main pages, and
confirm that damaged or wrong-passphrase backups fail loudly rather than restoring quietly.
"""

from __future__ import annotations

import gzip
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))

import backup  # noqa: E402
import restore  # noqa: E402

PASSPHRASE = "correct-horse-battery-staple-1234"


def make_db(path: Path, rows: int = 50) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE lead_events (id INTEGER PRIMARY KEY, customer_name TEXT)")
    conn.executemany(
        "INSERT INTO lead_events (customer_name) VALUES (?)",
        [(f"customer {i}",) for i in range(rows)],
    )
    conn.commit()
    conn.close()


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "leadlens.db"
        make_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_preserves_every_row(self):
        out = self.tmp / "b.age"
        backup.snapshot(self.db, self.tmp / "snap.db")
        blob = backup.encrypt(gzip.compress((self.tmp / "snap.db").read_bytes()), PASSPHRASE)
        out.write_bytes(blob)

        recovered = restore.recover(out, PASSPHRASE)
        (self.tmp / "r.db").write_bytes(recovered)
        conn = sqlite3.connect(self.tmp / "r.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM lead_events").fetchone()[0], 50)
        self.assertEqual(
            conn.execute("SELECT customer_name FROM lead_events WHERE id=7").fetchone()[0],
            "customer 6",
        )
        conn.close()

    def test_snapshot_captures_writes_still_sitting_in_the_wal(self):
        """The reason a plain file copy is unsafe: recent commits live in the -wal file."""
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO lead_events (customer_name) VALUES ('in-wal-only')")
        conn.commit()
        # Deliberately do NOT checkpoint or close -- the row is committed but may not be in the
        # main .db file yet. A cp would miss it; the backup API must not.
        snap = self.tmp / "snap.db"
        backup.snapshot(self.db, snap)
        conn.close()

        got = sqlite3.connect(snap)
        found = got.execute(
            "SELECT COUNT(*) FROM lead_events WHERE customer_name='in-wal-only'"
        ).fetchone()[0]
        got.close()
        self.assertEqual(found, 1, "snapshot lost a committed row still in the WAL")

    def test_wrong_passphrase_is_rejected(self):
        blob = backup.encrypt(gzip.compress(b"payload"), PASSPHRASE)
        path = self.tmp / "b.age"
        path.write_bytes(blob)
        with self.assertRaises(ValueError):
            restore.recover(path, "a-completely-different-passphrase")

    def test_tampered_ciphertext_is_rejected(self):
        """AES-GCM is authenticated, so a flipped bit must fail rather than decrypt to garbage."""
        blob = bytearray(backup.encrypt(gzip.compress(b"payload"), PASSPHRASE))
        blob[-1] ^= 0x01
        path = self.tmp / "b.age"
        path.write_bytes(bytes(blob))
        with self.assertRaises(ValueError):
            restore.recover(path, PASSPHRASE)

    def test_tampered_header_is_rejected(self):
        """The header is authenticated as associated data, so editing the salt must fail too."""
        blob = bytearray(backup.encrypt(gzip.compress(b"payload"), PASSPHRASE))
        blob[len(backup.MAGIC)] ^= 0xFF  # first byte of the salt
        path = self.tmp / "b.age"
        path.write_bytes(bytes(blob))
        with self.assertRaises(ValueError):
            restore.recover(path, PASSPHRASE)

    def test_truncated_file_is_rejected(self):
        blob = backup.encrypt(gzip.compress(b"payload"), PASSPHRASE)
        path = self.tmp / "b.age"
        path.write_bytes(blob[: len(blob) // 2])
        with self.assertRaises(ValueError):
            restore.recover(path, PASSPHRASE)

    def test_foreign_file_is_rejected_by_magic(self):
        path = self.tmp / "b.age"
        path.write_bytes(b"this is just some other file entirely")
        with self.assertRaises(ValueError) as ctx:
            restore.recover(path, PASSPHRASE)
        self.assertIn("magic", str(ctx.exception))

    def test_each_backup_uses_a_fresh_salt_and_nonce(self):
        a = backup.encrypt(b"same input", PASSPHRASE)
        b = backup.encrypt(b"same input", PASSPHRASE)
        self.assertNotEqual(a, b, "identical output means salt/nonce reuse")

    def test_describe_reports_row_counts(self):
        summary = restore.describe(self.db)
        self.assertIn("integrity=ok", summary)
        self.assertIn("lead_events=50", summary)


if __name__ == "__main__":
    unittest.main()
