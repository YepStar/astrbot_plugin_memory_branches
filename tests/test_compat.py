import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from astrbot_plugin_memory_branches.engine import Engine
from astrbot_plugin_memory_branches.compat import PublicMemory


class CompatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.engine=Engine(Path(self.tmp.name)/'db')
        self.bank=self.engine.create_bank('公共',['group:123456','xiaoheihe:public','bilibili:public','bilibili:self'])
        self.api=PublicMemory(NS(engine=self.engine,kick=lambda bank:None),self.bank)
    def tearDown(self):self.tmp.cleanup()
    def test_bili_writes_and_datasets_never_touch_private(self):
        self.api.put_bili({'rpid':'1','text':'讨论音乐','scope':'bili_comment'})
        self.assertEqual(self.api.bili_records()[0]['text'],'讨论音乐')
        self.api.put_dataset('video_memory.json',{'BV1':{'summary':'视频介绍'}})
        self.assertEqual(self.api.get_dataset('video_memory.json')['BV1']['summary'],'视频介绍')
        with self.assertRaises(PermissionError):self.api.put('qq_bot:FriendMessage:1','1','私聊')
        with self.assertRaises(PermissionError):self.api.put('bilibili:public','1','私聊',payload={'umo':'qq_bot:FriendMessage:1'})
    def test_record_updates_are_append_only_and_latest_version_wins(self):
        self.api.put_bili({'rpid':'1','text':'旧内容','scope':'bili_comment'})
        self.api.put_bili({'rpid':'1','text':'新内容','scope':'bili_comment'})
        self.assertEqual(len(self.api.rows()),2)
        self.assertEqual(len(self.api.bili_records()),1)
        self.assertEqual(self.api.bili_records()[0]['text'],'新内容')
        self.api.replace_bili([])
        self.assertEqual(self.api.bili_records(),[])
        self.assertEqual(len(self.api.rows()),2)

class SourceMappingTests(unittest.TestCase):
    def test_public_group_access_uses_bound_source_not_a_fixed_account(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(Path(temp) / 'memory.db')
            bank = engine.create_bank('公开', ['group:777'])
            api = PublicMemory(NS(engine=engine, kick=lambda bank: None), bank)
            api.put('group:777', 'one', '讨论音乐')
            self.assertEqual(api.search('音乐', 'group:777')[0]['scope'], 'group:777')
            self.assertEqual(api.search('音乐', 'group:888'), [])

    def test_migration_requires_distinct_explicit_private_accounts(self):
        from astrbot_plugin_memory_branches.migration import migrate
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'new.db'
            for first, second in [('group:1', 'qq:FriendMessage:2'), ('qq:FriendMessage:1', 'qq:FriendMessage:1')]:
                with self.assertRaises(ValueError):
                    migrate(Path(temp), destination, private_route=first, new_private_route=second)
                self.assertFalse(destination.exists())
