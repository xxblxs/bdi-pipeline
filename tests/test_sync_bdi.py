import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import sync_bdi


class SyncBdiTests(unittest.TestCase):
    def setUp(self):
        self.token = "NAVGREEN_test-only-token"
        token_patch = patch.object(sync_bdi, "SYNC_TOKEN", self.token)
        token_patch.start()
        self.addCleanup(token_patch.stop)
        request_patch = patch.object(sync_bdi.requests, "post")
        self.post = request_patch.start()
        self.addCleanup(request_patch.stop)
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {
            "code": 200, "msg": "波罗的海指数同步完成",
        }
        self.output = io.StringIO()
        output_patch = contextlib.redirect_stdout(self.output)
        output_patch.__enter__()
        self.addCleanup(output_patch.__exit__, None, None, None)
        self.payload = {"date": "2026-11-13", "BDI": 2980}

    def test_sends_authenticated_payload(self):
        self.assertTrue(sync_bdi.post_one(self.payload["date"], self.payload))
        args, kwargs = self.post.call_args
        self.assertEqual(args[0], sync_bdi.SYNC_URL)
        self.assertEqual(kwargs["headers"]["token"], self.token)
        self.assertEqual(kwargs["json"], self.payload)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn(self.token, self.output.getvalue())

    def test_http_200_auth_and_permission_errors_fail(self):
        for code in (4002, 4003, 4007):
            with self.subTest(code=code):
                self.post.return_value.json.return_value = {
                    "code": code, "msg": "无效的token或token已过期",
                }
                self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))

    def test_http_failure_cannot_be_overridden_by_body(self):
        self.post.return_value.status_code = 503
        self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))

    def test_malformed_response_fails(self):
        self.post.return_value.json.side_effect = ValueError("not JSON")
        self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))

    def test_missing_or_unexpected_business_code_fails(self):
        for body in ({}, [], {"code": "200"}, {"code": True}):
            with self.subTest(body=body):
                self.post.return_value.json.return_value = body
                self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))

    def test_missing_token_prevents_request(self):
        with patch.object(sync_bdi, "SYNC_TOKEN", ""):
            self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))
        self.post.assert_not_called()

    def test_connection_error_fails_without_exposing_credentials(self):
        self.post.side_effect = requests.RequestException(self.token)
        self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))
        self.assertNotIn(self.token, self.output.getvalue())

    def test_response_cannot_echo_token_to_logs(self):
        self.post.return_value.json.return_value = {"code": 4002, "msg": self.token}
        self.assertFalse(sync_bdi.post_one(self.payload["date"], self.payload))
        self.assertNotIn(self.token, self.output.getvalue())

    def test_cli_exits_nonzero_for_http_200_business_failure(self):
        self.post.return_value.json.return_value = {"code": 4002}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bdi_2026-11-13.json"
            path.write_text(json.dumps(self.payload), encoding="utf-8")
            with patch.object(sync_bdi, "JSON_DIR", Path(directory)):
                with patch("sys.argv", ["sync_bdi.py", "--last", "7"]):
                    with self.assertRaises(SystemExit) as error:
                        sync_bdi.main()
        self.assertEqual(error.exception.code, 1)

    def test_cli_exits_nonzero_before_requests_when_token_is_missing(self):
        with patch.object(sync_bdi, "SYNC_TOKEN", ""):
            with patch("sys.argv", ["sync_bdi.py", "--last", "7"]):
                with self.assertRaises(SystemExit) as error:
                    sync_bdi.main()
        self.assertEqual(error.exception.code, 1)
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
