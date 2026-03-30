from django.test import TestCase, override_settings

from banking.token_storage import decrypt_plaid_access_token, encrypt_plaid_access_token


class TokenStorageTests(TestCase):
    @override_settings(PLAID_TOKEN_ENCRYPTION_KEY="")
    def test_no_key_roundtrip_plain(self):
        t = "access-sandbox-abc"
        self.assertEqual(encrypt_plaid_access_token(t), t)
        self.assertEqual(decrypt_plaid_access_token(t), t)

    def test_fernet_roundtrip(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        with self.settings(PLAID_TOKEN_ENCRYPTION_KEY=key):
            raw = "access-sandbox-xyz"
            enc = encrypt_plaid_access_token(raw)
            self.assertNotEqual(enc, raw)
            self.assertEqual(decrypt_plaid_access_token(enc), raw)
