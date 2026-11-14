import os, tempfile, time, unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import license_key as lk


class LicenseKeyTest(unittest.TestCase):
    def setUp(self):
        self.priv = Ed25519PrivateKey.generate()
        self.pub = self.priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

    def key_for(self, did):
        return lk.format_key(self.priv.sign(lk._message(did)))

    def test_key_works_only_for_its_dash(self):
        key = self.key_for("ABC123")
        self.assertTrue(lk.is_valid(key, "ABC123", self.pub))
        self.assertFalse(lk.is_valid(key, "ABC124", self.pub))

    def test_pasted_formatting_is_accepted(self):
        key = self.key_for("ABC123")
        self.assertTrue(lk.is_valid("  " + key.lower().replace("-", " ") + "\n", "ABC123", self.pub))

    def test_tampered_or_garbage_key_fails(self):
        key = self.key_for("ABC123")
        bad = ("B" if key[0] == "A" else "A") + key[1:]
        for k in [bad, "", "not a key", key[:-5]]:
            self.assertFalse(lk.is_valid(k, "ABC123", self.pub), k)

    def test_other_signers_key_fails(self):
        other = Ed25519PrivateKey.generate()
        key = lk.format_key(other.sign(lk._message("ABC123")))
        self.assertFalse(lk.is_valid(key, "ABC123", self.pub))

    def test_unset_public_key_rejects_everything(self):
        self.assertFalse(lk.is_valid(self.key_for("ABC123"), "ABC123", "REPLACE_ME"))

    def test_dash_id_falls_back_to_mac(self):
        self.assertEqual(lk.dash_id("SN42\n", "aa:bb"), "SN42")
        self.assertEqual(lk.dash_id("0123456789ABCDEF", "AA:BB:CC:DD:EE:FF\n"), "mac-aa:bb:cc:dd:ee:ff")
        self.assertIsNone(lk.dash_id("", "00:00:00:00:00:00"))
        self.assertIsNone(lk.dash_id(None, None))
        self.assertFalse(lk.is_valid(self.key_for("x"), None, self.pub))



class BackupTest(unittest.TestCase):
    """Undo needs no key, so it must not be a way to unlock a dash."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def bak(self, did, name, on, age=0):
        """Backup made the way do_apply makes it: in the dash's own ID folder."""
        folder = lk.prepare_backup_dir(self.base, did) if did else self.base
        p = folder / f"zkapp.cfg.{name}.bak"
        p.write_text('{"a" : 1, "uv_app_use" : %s}' % ("true" if on else "false"))
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_never_restores_another_dashs_backup(self):
        self.bak("DASH-A", "1", on=False)
        self.assertIsNone(lk.pick_backup(self.base, "DASH-B"))

    def test_never_restores_a_config_with_carplay_on(self):
        older = self.bak("DASH-A", "1", on=False, age=100)
        self.bak("DASH-A", "2", on=True)
        self.assertEqual(lk.pick_backup(self.base, "DASH-A"), older)
        older.unlink()
        self.assertIsNone(lk.pick_backup(self.base, "DASH-A"))

    def test_backups_outside_a_dash_folder_are_never_used(self):
        self.bak(None, "old", on=False)
        self.assertIsNone(lk.pick_backup(self.base, "DASH-A"))

    def test_folder_without_id_file_is_never_used(self):
        d = lk.backup_dir(self.base, "DASH-A"); d.mkdir()
        (d / "zkapp.cfg.1.bak").write_text('{"uv_app_use" : false}')
        self.assertIsNone(lk.pick_backup(self.base, "DASH-A"))

    def test_ids_sharing_a_folder_name_never_mix(self):
        # "mac-aa:bb" and "mac-aa_bb" clean up to the same folder name
        own = self.bak("mac-aa:bb", "1", on=False)
        self.assertEqual(lk.backup_dir(self.base, "mac-aa_bb"), own.parent)
        self.assertIsNone(lk.pick_backup(self.base, "mac-aa_bb"))
        self.assertIsNone(lk.prepare_backup_dir(self.base, "mac-aa_bb"))
        self.assertEqual(lk.pick_backup(self.base, "mac-aa:bb"), own)

    def test_no_id_never_restores(self):
        self.bak("DASH-A", "1", on=False)
        self.assertIsNone(lk.pick_backup(self.base, None))

    def test_backup_dir_is_safe_for_mac_ids(self):
        self.assertEqual(lk.backup_dir(self.base, "mac-aa:bb/../x").name, "dash-mac-aa_bb_.._x")
        self.assertEqual(lk.backup_dir(self.base, "..").parent, self.base)


if __name__ == "__main__":
    unittest.main()
