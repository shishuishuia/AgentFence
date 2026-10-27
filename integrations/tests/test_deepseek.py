"""No paid calls: validate the transport protocol and secret-safe failures."""

import io
import json
import unittest
from urllib.error import HTTPError

from integrations.deepseek import DeepSeekClient, DeepSeekConfig, ModelError


class Opener:
    def __init__(self, value):
        self.value = value
        self.request = None

    def open(self, request, timeout):
        self.request = request
        if isinstance(self.value, Exception):
            raise self.value
        return io.BytesIO(json.dumps(self.value).encode())


class DeepSeekTests(unittest.TestCase):
    def test_tool_protocol_uses_requested_model_and_discards_reasoning(self):
        client = DeepSeekClient(DeepSeekConfig("fake-private-key"))
        client._opener = Opener({"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "reasoning_content": "private reasoning", "tool_calls": [{
                "id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"src/main.rs"}'},
            }],
        }}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
        message, usage = client.complete([{"role": "user", "content": "test"}], [])
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "read_file")
        self.assertNotIn("reasoning_content", message)
        body = json.loads(client._opener.request.data)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(client._opener.request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertNotIn("fake-private-key", json.dumps(body))
        self.assertEqual(usage["total_tokens"], 7)

    def test_auth_errors_do_not_echo_the_provider_body_or_key(self):
        client = DeepSeekClient(DeepSeekConfig("fake-private-key"))
        client._opener = Opener(HTTPError('https://api.deepseek.com', 401, 'fake-private-key', {}, None))
        with self.assertRaises(ModelError) as caught:
            client.complete([], [])
        self.assertNotIn('fake-private-key', str(caught.exception))
        self.assertIn('认证失败', str(caught.exception))

    def test_truncation_and_invalid_shapes_are_failures(self):
        client = DeepSeekClient(DeepSeekConfig("fake-private-key"))
        for value in ({}, {"choices": [None]}, {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}):
            client._opener = Opener(value)
            with self.assertRaises(ModelError):
                client.complete([], [])


if __name__ == "__main__":
    unittest.main()
