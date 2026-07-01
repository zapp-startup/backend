"""Unit tests for config.settings.banking_env helpers."""
from unittest.mock import patch

from django.test import SimpleTestCase


class BankingEnvBoolTests(SimpleTestCase):
    def test_unset_returns_default(self):
        from config.settings.banking_env import env_bool

        with patch("config.settings.banking_env.os.getenv", return_value=None):
            self.assertFalse(env_bool("BANKING_REQUIRE_MFA", False))
            self.assertTrue(env_bool("BANKING_REQUIRE_MFA", True))

    def test_truthy_strings(self):
        from config.settings.banking_env import env_bool

        for val in ("1", "true", "yes", "TRUE", " True "):
            with patch("config.settings.banking_env.os.getenv", return_value=val):
                self.assertTrue(env_bool("X", False))

    def test_falsy_strings(self):
        from config.settings.banking_env import env_bool

        for val in ("0", "false", "no", "", "maybe"):
            with patch("config.settings.banking_env.os.getenv", return_value=val):
                self.assertFalse(env_bool("X", True))
