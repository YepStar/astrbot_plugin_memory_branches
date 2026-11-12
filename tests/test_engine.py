import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from astrbot_plugin_memory_branches.engine import Engine, validate_summary
from astrbot_plugin_memory_branches.service import Service, learn_style


def summary(text="喜欢音乐和阅读", importance=0.8):
    return {
        "summary": text,
        "importance": importance,
        "kind": "preference",
        "topics": ["音乐"],
        "participants": ["用户"],
        "key_facts": ["喜欢音乐"],
        "atoms": [
            {"content": "喜欢音乐", "kind": "preference", "importance": importance}
        ],
    }


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / "memory.db")
        self.a = self.engine.create_bank(
            "私聊甲",
            ["qq_bot:FriendMessage:1"],
            {"qq_bot:FriendMessage:1": {"initial": True}},
        )
        self.b = self.engine.create_bank("私聊乙", ["qq_bot:FriendMessage:2"])

    def tearDown(self):
        self.tmp.cleanup()

    def pair(self, bank, key, text="喜欢音乐", created=None):
        source = self.engine.bank(bank)["routes"][0]
        self.engine.append(bank, source, key, "user", text, timestamp=created)
        self.engine.append(
            bank,
            source,
            key,
            "assistant",
            "记住了",
            timestamp=(created + 0.1 if created else None),
            host={"checkpoint": key},
        )

    def commit(self, bank, text="喜欢音乐和阅读"):
        return self.engine.commit(self.engine.plan(bank, manual=True), summary(text))

    def test_source_exclusive(self):
        with self.assertRaises(ValueError):
            self.engine.create_bank("重复", ["qq_bot:FriendMessage:1"])
        with self.assertRaises(ValueError):
            self.engine.create_bank(
                "混合", ["qq_bot:FriendMessage:3", "bilibili:public"]
            )
        with self.assertRaises(PermissionError):
            self.engine.append(self.a, "qq_bot:FriendMessage:2", "x", "user", "越界")
        self.assertEqual(self.engine.route("qq_bot:FriendMessage:1"), self.a)

    def test_auto_ten_rounds_manual_one_round(self):
        self.pair(self.a, "1")
        self.assertIsNone(self.engine.plan(self.a))
        self.assertIsNotNone(self.engine.plan(self.a, manual=True))
        for i in range(2, 11):
            self.pair(self.a, str(i))
        self.assertEqual(len(self.engine.plan(self.a)["messages"]), 20)

    def test_pending_user_is_not_checkpointed(self):
        self.engine.append(self.a, "qq_bot:FriendMessage:1", "1", "user", "未回复")
        self.assertIsNone(self.engine.plan(self.a, manual=True))

    def test_recall_and_graph_do_not_cross_banks(self):
        self.pair(self.a, "1")
        self.commit(self.a)
        self.pair(self.b, "1")
        self.commit(self.b, "喜欢围棋")
        self.assertTrue(self.engine.recall(self.a, "音乐")["matches"])
        self.assertFalse(
            self.engine.recall(self.b, "音乐")["matches"][0]["content"]
            == "喜欢音乐和阅读"
        )
        mids = {m["id"] for m in self.engine.memories(self.a)}
        self.assertFalse(
            any(n.get("memory_id") in mids for n in self.engine.graph(self.b)["nodes"])
        )

    def test_rollback_restores_snapshot_and_removes_entire_suffix(self):
        self.pair(self.a, "1")
        first = self.commit(self.a)
        original = self.engine.memories(self.a)
        self.engine.put_dataset(
            self.a, "styles.json", {"patterns": [{"example": "你好"}]}
        )
        self.pair(self.a, "2")
        second = self.commit(self.a, "今天计划阅读一本书")
        self.pair(self.a, "3")
        third = self.commit(self.a, "讨论了围棋")
        self.pair(self.a, "4", "尚未总结")
        self.pair(self.b, "1")
        self.commit(self.b, "另一个人的生活")
        other = self.engine.state(self.b)
        self.engine.update_memory(
            self.a, original[0]["id"], importance=0.1, status="archived"
        )
        self.engine.recall(self.a, "音乐", track=True)
        preview = self.engine.preview_rollback(self.a, second)
        self.assertEqual(preview["target_node"], first)
        self.assertEqual(preview["removed_nodes"], 2)
        self.assertEqual(preview["removed_messages"], 6)
        self.engine.begin_rollback(self.a, second, preview["revision"])
        with self.assertRaises(ValueError):
            self.pair(self.a, "blocked")
        self.engine.finish_rollback(self.a)
        self.assertEqual(self.engine.memories(self.a), original)
        self.assertIsNone(self.engine.dataset(self.a, "styles.json"))
        self.assertEqual(self.engine.state(self.a)["pending_count"], 0)
        self.assertEqual(len(self.engine.state(self.a)["nodes"]), 1)
        self.assertEqual(self.engine.state(self.b), other)

    def test_stale_model_and_delete_preview_are_rejected(self):
        self.pair(self.a, "1")
        first = self.commit(self.a)
        self.pair(self.a, "2")
        plan = self.engine.plan(self.a, manual=True)
        preview = self.engine.preview_rollback(self.a, first)
        self.engine.put_dataset(self.a, "styles.json", {})
        self.assertIsNone(self.engine.commit(plan, summary()))
        with self.assertRaises(ValueError):
            self.engine.begin_rollback(self.a, first, preview["revision"])

    def test_invalid_summary_does_not_advance(self):
        self.pair(self.a, "1")
        plan = self.engine.plan(self.a, manual=True)
        with self.assertRaises(ValueError):
            self.engine.commit(plan, {"summary": "", "importance": 0.4})
        self.assertEqual(self.engine.state(self.a)["pending_count"], 2)

    def test_dedup_snapshot_restores_previous_version(self):
        self.pair(self.a, "1")
        first = self.commit(self.a)
        before = self.engine.memories(self.a)
        self.pair(self.a, "2")
        second = self.commit(self.a)
        self.assertEqual(len(self.engine.memories(self.a)), 1)
        preview = self.engine.preview_rollback(self.a, second)
        self.engine.begin_rollback(self.a, second, preview["revision"])
        self.engine.finish_rollback(self.a)
        self.assertEqual(self.engine.memories(self.a), before)

    def test_lifecycle_and_preview_no_access_mutation(self):
        self.pair(self.a, "1")
        self.commit(self.a)
        memory = self.engine.memories(self.a)[0]
        self.engine.recall(self.a, "音乐")
        self.assertEqual(self.engine.memories(self.a)[0]["accesses"], 0)
        self.engine.maintain(time.time() + 40 * 86400)
        self.assertLess(
            self.engine.memories(self.a)[0]["importance"], memory["importance"]
        )
        self.assertEqual(
            self.engine.memories(self.a)[0]["metadata"]["atoms"][0]["status"], "active"
        )
        self.engine.update_memory(
            self.a, memory["id"], importance=0.1, status="archived"
        )
        self.assertFalse(self.engine.recall(self.a, "音乐")["matches"])

    def test_import_idempotent_and_baseline(self):
        self.assertTrue(self.engine.import_memory(self.a, "legacy", "1", "喜欢音乐"))
        self.assertFalse(self.engine.import_memory(self.a, "legacy", "1", "喜欢音乐"))
        node = self.engine.seal_migration(self.a, {})
        self.assertEqual(self.engine.memories(self.a)[0]["node"], node)

    def test_migration_baseline_preserves_preexisting_unsummarized_tail_on_rollback(
        self,
    ):
        self.pair(self.a, "old")
        self.engine.import_memory(self.a, "legacy", "old", "已有长期记忆")
        self.engine.seal_migration(self.a, {}, summary_cursor=0)
        initial = self.engine.state(self.a)
        node = self.commit(self.a)
        preview = self.engine.preview_rollback(self.a, node)
        self.assertEqual(preview["removed_messages"], 0)
        self.engine.begin_rollback(self.a, node, preview["revision"])
        self.engine.finish_rollback(self.a)
        self.assertEqual(
            self.engine.state(self.a)["pending_count"], initial["pending_count"]
        )
        self.assertEqual(
            self.engine.state(self.a)["message_count"], initial["message_count"]
        )

    def test_day_and_gap_split_complete_turns(self):
        now = time.time() - 100000
        self.pair(self.a, "1", created=now)
        self.pair(self.a, "2", created=now + 20000)
        plan = self.engine.plan(self.a, manual=True)
        self.assertEqual(len(plan["messages"]), 2)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name) / "memory.db")
        self.a = self.engine.create_bank("甲", ["qq_bot:FriendMessage:1"])
        self.b = self.engine.create_bank("乙", ["qq_bot:FriendMessage:2"])
        for bank in [self.a, self.b]:
            source = self.engine.bank(bank)["routes"][0]
            self.engine.append(bank, source, "1", "user", "我喜欢音乐")
            self.engine.append(bank, source, "1", "assistant", "好的")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_banks_summarize_in_parallel_and_duplicate_click_deduplicates(self):
        calls = []

        async def model(bank, system, prompt):
            calls.append(bank)
            await asyncio.sleep(0.01)
            return summary()

        service = Service(self.engine, model)
        service.start_summary(self.a)
        self.assertTrue(service.start_summary(self.a)["already_running"])
        service.start_summary(self.b)
        await asyncio.gather(*service.tasks.values())
        self.assertEqual(set(calls), {self.a, self.b})
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.engine.state(self.a)["pending_count"], 0)
        self.assertEqual(service.start_summary(self.a)["status"], "empty")
        await service.close()

    async def test_failed_summary_preserves_cursor(self):
        async def model(*args):
            raise RuntimeError("unavailable")

        service = Service(self.engine, model)
        service.start_summary(self.a)
        await service.tasks[self.a]
        self.assertEqual(service.jobs[self.a]["status"], "error")
        self.assertEqual(self.engine.state(self.a)["pending_count"], 2)
        await service.close()

    async def test_rollback_journal_recovers_after_host_failure(self):
        node = self.engine.commit(self.engine.plan(self.a, manual=True), summary())

        class Host:
            failing = True

            async def restore(self, *args):
                if self.failing:
                    raise OSError("disk error")

        host = Host()
        service = Service(self.engine, None, host=host)
        with self.assertRaises(OSError):
            await service.rollback(self.a, node, self.engine.bank(self.a)["revision"])
        self.assertTrue(self.engine.state(self.a)["recovering"])
        host.failing = False
        await service.recover()
        self.assertFalse(self.engine.state(self.a)["recovering"])
        self.assertEqual(self.engine.state(self.a)["memory_count"], 0)

    async def test_style_learning_only_copies_real_user_examples(self):
        async def model(*args):
            return {
                "patterns": [
                    {"description": "直接表达偏好", "example": "我喜欢音乐"},
                    {"description": "伪造", "example": "我喜欢别的东西"},
                ]
            }

        service = Service(self.engine, model)
        result = await learn_style(service, self.a)
        self.assertEqual(result["learned"], 1)
        self.assertEqual(
            self.engine.dataset(self.a, "styles.json")["patterns"][0]["example"],
            "我喜欢音乐",
        )
        self.assertIsNone(self.engine.dataset(self.b, "styles.json"))

class StyleEditingTests(unittest.TestCase):
    def test_delete_only_selected_style_and_reject_stale_learning(self):
        with tempfile.TemporaryDirectory() as root:
            engine = Engine(Path(root) / 'memory.db')
            a = engine.create_bank('A', ['qq_bot:FriendMessage:1'])
            b = engine.create_bank('B', ['qq_bot:FriendMessage:2'])
            styles = {'cursor': 42, 'enabled': True, 'updated': 123,
                      'patterns': [{'id': 'old', 'enabled': True}, {'id': 'new', 'enabled': False}]}
            engine.put_dataset(a, 'styles.json', styles)
            engine.put_dataset(b, 'styles.json', styles)
            revision = engine.bank(a)['revision']
            self.assertEqual(engine.update_styles(a, pattern_id='new', delete=True), {'deleted': 'new'})
            expected = {**styles, 'patterns': [styles['patterns'][0]]}
            self.assertEqual(Engine(Path(root) / 'memory.db').dataset(a, 'styles.json'), expected)
            self.assertEqual(engine.dataset(b, 'styles.json'), styles)
            self.assertFalse(engine.put_dataset(a, 'styles.json', styles, revision=revision))
            with self.assertRaises(ValueError):
                engine.update_styles(a, pattern_id='new', delete=True)
            with self.assertRaises(ValueError):
                engine.update_styles(a, delete=True)
            engine.update_styles(a, pattern_id='old', pattern_enabled=False)
            self.assertFalse(engine.dataset(a, 'styles.json')['patterns'][0]['enabled'])
            self.assertEqual(engine.dataset(a, 'styles.json')['cursor'], 42)
            engine.update_styles(a, enabled=False)
            self.assertFalse(engine.dataset(a, 'styles.json')['enabled'])
