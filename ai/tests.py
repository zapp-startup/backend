from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

# Create your tests here.
from ai.openai_config import get_openai_api_key, require_openai_api_key


class OpenAIConfigTests(SimpleTestCase):
    @override_settings(OPENAI_API_KEY="")
    def test_get_openai_api_key_returns_none_when_empty(self):
        self.assertIsNone(get_openai_api_key())

    @override_settings(OPENAI_API_KEY="  test-key  ")
    def test_get_openai_api_key_returns_trimmed_value(self):
        self.assertEqual(get_openai_api_key(), "test-key")

    @override_settings(OPENAI_API_KEY=None)
    def test_require_openai_api_key_raises_when_missing(self):
        with self.assertRaises(ImproperlyConfigured):
            require_openai_api_key()