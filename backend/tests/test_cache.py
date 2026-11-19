import json
import os
import time
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

    def test_account_data_hit_uses_one_cache_read(self):
        cache.get_or_load("alice", "balance", lambda: {"amount": 10})
        with patch.object(self.client, "get", wraps=self.client.get) as read:
            self.assertEqual(cache.get_or_load("alice", "balance", lambda: None), {"amount": 10})
            self.assertEqual(read.call_count, 1)

    def test_inflight_read_does_not_restore_stale_value(self):
        def load():
            cache.invalidate_account("alice")
            return {"amount": 10}

        cache.get_or_load("alice", "balance", load)
        self.assertEqual(cache.get_or_load("alice", "balance", lambda: {"amount": 20}), {"amount": 20})

    def test_all_account_kinds_cached_and_cleared_by_payment(self):
        for kind in cache.KINDS:
            self.assertEqual(cache.get_or_load("alice", kind, lambda: {"v": 1}), {"v": 1})
            self.assertEqual(cache.get_or_load("alice", kind, lambda: {"v": 2}), {"v": 1})
        cache.invalidate_account("alice")
        for kind in cache.KINDS:
            self.assertEqual(cache.get_or_load("alice", kind, lambda: {"v": 3}), {"v": 3})

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


class AccountAnswerTests(unittest.TestCase):
    def test_inspect_shows_only_selected_users_live_entries_without_touching_lru(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        pipe = client.pipeline.return_value
        entry = {"question": "my balance", "answer": "10", "expires": time.time() + 60, "embedding": [1], "sources": ["balance"]}
        expired = {**entry, "answer": "old", "expires": time.time() - 1}
        pipe.execute.return_value = [1, 50] + [0, -2] * len(cache.KINDS) + [{"a": json.dumps(entry), "b": json.dumps(expired)}]
        with patch.object(cache, "_client", return_value=client):
            result = cache.inspect_account_cache("alice")
        self.assertEqual(result["data"], [{"kind": "balance", "ttl_seconds": 50}])
        self.assertEqual([a["answer"] for a in result["answers"]], ["10"])
        pipe.hgetall.assert_called_once_with(cache._answer_key("alice"))
        client.eval.assert_not_called()

    def test_threshold_is_strictly_above_085(self):
        self.enterContext(patch.dict(os.environ, {"ACCOUNT_SEMANTIC_THRESHOLD": "0.85"}))
        entry = {"embedding": [1.0, 0.0], "answer": "a"}
        at = [0.85, (1 - 0.85 ** 2) ** 0.5]
        above = [0.86, (1 - 0.86 ** 2) ** 0.5]
        self.assertIsNone(cache.best_semantic_match(at, [entry]))
        self.assertEqual(cache.best_semantic_match(above, [entry]), entry)
        self.assertIsNone(cache.best_semantic_match([0.0, 1.0], [entry]))

    @unittest.skipUnless(os.getenv("TEST_VALKEY_URL"), "set TEST_VALKEY_URL to a disposable Valkey/Redis")
    def test_lru_keeps_four_users_and_isolates(self):
        client = valkey.Valkey.from_url(os.environ["TEST_VALKEY_URL"], decode_responses=True)
        client.flushdb()
        with patch.object(cache, "_client", return_value=client), patch.dict(os.environ, {"ACCOUNT_CACHE_MAX_USERS": "4"}):
            for user in ["u1", "u2", "u3", "u4"]:
                cache.put_account_answer(user, cache.answer_snapshot(user), "q", "a " + user, ["s"], [1.0, 0.0])
                time.sleep(0.01)
            cache.read_account_answers("u1")
            self.assertEqual(cache.read_account_answers("u1")[0]["embedding"], [1.0, 0.0])
            self.assertIsNotNone(cache.best_semantic_match([1.0, 0.0], cache.read_account_answers("u1")))
            listed = cache.list_account_answer_users("u1", 25, 0)
            self.assertEqual(listed["total_users"], 1)
            self.assertEqual([(row["username"], row["answer"]) for row in listed["rows"]], [("u1", "a u1")])
            cache.put_account_answer("u5", cache.answer_snapshot("u5"), "q", "a u5", ["s"], [1.0, 0.0])
            self.assertEqual(cache.read_account_answers("u2"), [])
            self.assertIsNone(client.get(cache._answer_key("u2") + ":owner"))
            self.assertEqual([e["answer"] for e in cache.read_account_answers("u1")], ["a u1"])
            self.assertEqual([e["answer"] for e in cache.read_account_answers("u5")], ["a u5"])
            cache.invalidate_account("u1")
            self.assertEqual(cache.read_account_answers("u1"), [])
            self.assertEqual(len(cache.read_account_answers("u5")), 1)
        client.flushdb()


if __name__ == "__main__":
    unittest.main()
