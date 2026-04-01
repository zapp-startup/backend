from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from banking.models import BankConnection
from banking.token_storage import (
    decrypt_plaid_access_token,
    encrypt_plaid_access_token,
    get_plaid_access_token_for_api,
    is_plaid_access_token_encrypted,
)
from users.models import User


class TokenStorageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="plaid_token_test",
            email="plaid-token@test.com",
            password="testpass123",
        )

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

    def test_rotation_keys_can_decrypt_legacy_ciphertext_and_rewrap_with_current_key(self):
        from cryptography.fernet import Fernet

        old_key = Fernet.generate_key().decode("ascii")
        new_key = Fernet.generate_key().decode("ascii")
        old_ciphertext = Fernet(old_key.encode("ascii")).encrypt(b"access-sandbox-rotated").decode("ascii")
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-rotated",
            plaid_access_token=old_ciphertext,
        )

        with self.settings(PLAID_TOKEN_ENCRYPTION_KEYS=f"{new_key},{old_key}", PLAID_TOKEN_ENCRYPTION_KEY=""):
            plaintext = get_plaid_access_token_for_api(connection)
            self.assertEqual(plaintext, "access-sandbox-rotated")

            connection.refresh_from_db()
            self.assertNotEqual(connection.plaid_access_token, old_ciphertext)
            self.assertEqual(
                Fernet(new_key.encode("ascii")).decrypt(connection.plaid_access_token.encode("ascii")).decode("utf-8"),
                "access-sandbox-rotated",
            )

    def test_get_token_for_api_upgrades_legacy_plaintext_row_when_key_is_set(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-legacy-token",
            plaid_access_token="access-sandbox-legacy",
        )

        with self.settings(PLAID_TOKEN_ENCRYPTION_KEY=key):
            token = get_plaid_access_token_for_api(connection)
            self.assertEqual(token, "access-sandbox-legacy")

            connection.refresh_from_db()
            self.assertNotEqual(connection.plaid_access_token, "access-sandbox-legacy")
            self.assertTrue(is_plaid_access_token_encrypted(connection.plaid_access_token))
            self.assertEqual(decrypt_plaid_access_token(connection.plaid_access_token), "access-sandbox-legacy")

    def test_encrypt_plaid_access_tokens_command_encrypts_plaintext_rows(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        plaintext = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-plaintext",
            plaid_access_token="access-sandbox-plain",
        )
        with self.settings(PLAID_TOKEN_ENCRYPTION_KEY=key):
            encrypted = BankConnection.objects.create(
                user=self.user,
                plaid_item_id="item-encrypted",
                plaid_access_token=encrypt_plaid_access_token("access-sandbox-encrypted"),
            )
            out = StringIO()
            call_command("encrypt_plaid_access_tokens", stdout=out)
            plaintext.refresh_from_db()
            encrypted.refresh_from_db()
            self.assertTrue(is_plaid_access_token_encrypted(plaintext.plaid_access_token))
            self.assertTrue(is_plaid_access_token_encrypted(encrypted.plaid_access_token))
            self.assertIn("Encrypted 1 Plaid access tokens", out.getvalue())

    def test_encrypt_plaid_access_tokens_command_supports_dry_run(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-dry-run",
            plaid_access_token="access-sandbox-plain",
        )

        with self.settings(PLAID_TOKEN_ENCRYPTION_KEY=key):
            out = StringIO()
            call_command("encrypt_plaid_access_tokens", "--dry-run", stdout=out)

        connection.refresh_from_db()
        self.assertEqual(connection.plaid_access_token, "access-sandbox-plain")
        self.assertIn("Dry run: 1 of 1 Plaid connections still store plaintext access tokens.", out.getvalue())
