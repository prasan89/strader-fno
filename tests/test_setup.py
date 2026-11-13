import os
import stat
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from strader_fno.setup import SetupContext, page
from strader_fno.settings import load_settings, write_settings
from strader_fno.http import ApiError


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / ".env"
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def transport(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/token"):
            return {"access_token": "new-private-token"}
        if url.endswith("/market/status/NSE"):
            return {"status": "success", "data": {"exchange": "NSE", "status": "normal"}}
        return {"ok": True, "result": {"id": 1}}

    def test_local_form_csrf_check_and_no_secret_echo(self):
        context = SetupContext(self.path, "http://127.0.0.1:8765", self.transport)
        context.validate_csrf(context.origin, context.csrf)
        with self.assertRaises(ValueError):
            context.validate_csrf("https://untrusted.example", context.csrf)
        with self.assertRaises(ValueError):
            context.validate_csrf(context.origin, "wrong")
        self.assertNotIn("new-private-token", page(context))

    def test_oauth_state_is_checked_and_consumed_before_exchange(self):
        context = SetupContext(self.path, "http://127.0.0.1:8765", self.transport)
        redirect = context.configure({"auth_method": "oauth", "UPSTOX_CLIENT_ID": "client-id",
                                      "UPSTOX_CLIENT_SECRET": "private-client-secret",
                                      "TELEGRAM_BOT_TOKEN": "123:test-secret", "TELEGRAM_CHAT_ID": "chat"})
        query = parse_qs(urlsplit(redirect).query)
        self.assertEqual([context.origin + "/callback"], query["redirect_uri"])
        with self.assertRaises(ValueError):
            context.callback("wrong-state", "code")
        self.assertFalse(self.path.exists())
        context.callback(query["state"][0], "code")
        self.assertTrue(context.complete)
        self.assertEqual("new-private-token", load_settings(self.path)["UPSTOX_ACCESS_TOKEN"])
        self.assertEqual(0o600, stat.S_IMODE(self.path.stat().st_mode))
        with self.assertRaises(ValueError):
            context.callback(query["state"][0], "code")
        self.assertEqual(1, sum("sendMessage" in url for url, _ in self.calls))

    def test_tokens_are_validated_before_being_saved(self):
        context = SetupContext(self.path, "http://127.0.0.1:8765", self.transport)
        context.configure({"auth_method": "token", "UPSTOX_ACCESS_TOKEN": "private-token",
                           "TELEGRAM_BOT_TOKEN": "123:test-secret", "TELEGRAM_CHAT_ID": "chat"})
        self.assertTrue(context.complete)
        self.assertEqual(3, len(self.calls))

    def test_newline_credentials_are_rejected_before_any_request(self):
        context = SetupContext(self.path, "http://127.0.0.1:8765", self.transport)
        with self.assertRaises(ValueError) as failure:
            context.save_verified({"UPSTOX_ACCESS_TOKEN": "private\nsecret"})
        self.assertEqual([], self.calls)
        self.assertNotIn("private", str(failure.exception))

    def test_environment_override_and_no_shell_expansion(self):
        write_settings(self.path, {"UPSTOX_ACCESS_TOKEN": "$(no-shell-expansion)"})
        with patch.dict(os.environ, {"UPSTOX_ACCESS_TOKEN": "environment-token"}):
            self.assertEqual("environment-token", load_settings(self.path)["UPSTOX_ACCESS_TOKEN"])

    def test_chat_readable_but_delivery_forbidden_does_not_save_configuration(self):
        def transport(url, **kwargs):
            if url.endswith('/sendMessage'):
                raise ApiError('Telegram', 403)
            return self.transport(url, **kwargs)
        context = SetupContext(self.path, "http://127.0.0.1:8765", transport)
        with self.assertRaisesRegex(ValueError, "numeric Telegram user ID"):
            context.configure({"auth_method": "token", "UPSTOX_ACCESS_TOKEN": "private-token",
                               "TELEGRAM_BOT_TOKEN": "123:test-secret", "TELEGRAM_CHAT_ID": "chat"})
        self.assertFalse(self.path.exists())
        self.assertFalse(context.complete)


if __name__ == "__main__":
    unittest.main()
