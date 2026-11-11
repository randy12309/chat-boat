import json
import unittest
from unittest.mock import patch

import valkey

from mcp_server import cache


class FakeValkey:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if not nx or key not in self.values:
            self.values[key] = value

    def eval(self, script, count, *args):
        keys, values = args[:count], args[count:]
        if script == cache._PUT_IF_CURRENT:
            if self.values.get(keys[0]) == values[0]:
                self.values[keys[1]] = values[1]
        else:
            self.values[keys[0]] = values[0]
            for key in keys[1:]:
                self.values.pop(key, None)


class AccountCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeValkey()
        self.client_patch = patch.object(cache, "_client", return_value=self.client)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def test_isolation_and_invalidation(self):
        self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 10}), {"amount": 10})
        self.assertEqual(cache.get_or_load("bob", "balance", lambda: {"amount": 20}), {"amount": 20})
        self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 30}), {"amount": 10})
        cache.invalidate_account("alice")
        self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 30}), {"amount": 30})
        self.assertEqual(cache.get_or_load("bob", "balance", lambda: None), {"amount": 20})
        self.assertNotIn("alice", json.dumps(self.client.values))

    def test_inflight_read_does_not_restore_stale_value(self):
        def load():
            cache.invalidate_account("alice")
            return {"amount": 10}

        cache.get_or_load("alice", "balance", load)
        self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 20}), {"amount": 20})

    def test_errors_are_not_cached(self):
        error = {"status": "error", "message": "unavailable"}
        self.assertEqual(cache.get_or_load("alice", "eligibility", lambda: error), error)
        self.assertEqual(cache.get_or_load("alice", "eligibility", lambda: {"status": "ok"}), {"status": "ok"})

    def test_valkey_outage_falls_back_to_source(self):
        with patch.object(self.client, "get", side_effect=valkey.ConnectionError("offline")):
            self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 10}), {"amount": 10})

    def test_invalid_configuration_falls_back_to_source(self):
        with patch.object(cache, "_client", side_effect=ValueError("invalid URL")):
            self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 10}), {"amount": 10})
            cache.invalidate_account("alice")


if __name__ == "__main__":
    unittest.main()