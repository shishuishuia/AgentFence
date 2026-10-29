"""Credential storage tests: bounded format, write-only status, atomic save."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations.credentials import CredentialStore, validate


class ValidateTests(unittest.TestCase):
    def test_accepts_a_deepseek_style_key(self):
        key = "sk-" + "a" * 32
        self.assertEqual(validate(f"  {key}  "), key)

    def test_rejects_wrong_prefix_length_or_whitespace(self):
        for value in ("", "abc", "pk-" + "a" * 32, "sk-" + "a" * 10, "sk-" + "a" * 10 + " and more", "sk-" + "a" * 300):
            with self.assertRaises(ValueError):
                validate(value)

    def test_rejects_non_text(self):
        with self.assertRaises(ValueError):
            validate(None)


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "harness.key"
        self.environment = mock.patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        self.store = CredentialStore(self.path)

    def test_status_never_contains_the_key(self):
        key = "sk-" + "b" * 32
        state = self.store.save(key)
        self.assertEqual(state, {"configured": True, "source": "file", "editable": True})
        self.assertNotIn(key, repr(state))
        self.assertEqual(self.store.load(), key)

    def test_environment_takes_precedence_and_is_not_editable(self):
        self.store.save("sk-" + "c" * 32)
        os.environ["DEEPSEEK_API_KEY"] = "sk-" + "d" * 32
        self.assertEqual(self.store.status(), {"configured": True, "source": "environment", "editable": False})
        self.assertEqual(self.store.load(), "sk-" + "d" * 32)

    def test_clear_removes_only_the_file(self):
        self.store.save("sk-" + "e" * 32)
        self.assertEqual(self.store.clear()["configured"], False)
        self.assertFalse(self.path.exists())

    def test_missing_file_is_unconfigured(self):
        self.assertEqual(self.store.status(), {"configured": False, "source": None, "editable": True})
        self.assertEqual(self.store.load(), "")


if __name__ == "__main__":
    unittest.main()
