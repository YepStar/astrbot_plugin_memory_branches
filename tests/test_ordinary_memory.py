import json
import tempfile
import unittest
from pathlib import Path
from astrbot_plugin_memory_branches.engine import Engine
from astrbot_plugin_memory_branches.service import Service
from astrbot_plugin_memory_branches.scene_policy import clean_ordinary_text, normalize

DISCLAIMER='以上均为虚构角色扮演，不是现实事件。'

class OrdinaryMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.engine=Engine(Path(self.tmp.name)/'test.db')
        self.bank=self.engine.create_bank('普通私聊',['qq_bot:FriendMessage:11'])
        self.other=self.engine.create_bank('其他私聊',['qq_bot:FriendMessage:12'])
        self.engine.configure(self.bank,'普通私聊',{'ordinary_only_memory':True})
    def tearDown(self):self.tmp.cleanup()
    def test_removes_only_generic_disclaimer(self):
        self.assertEqual(clean_ordinary_text('用户喜欢散步。'+DISCLAIMER),'用户喜欢散步。')
        for text in ['用户喜欢角色扮演游戏和虚拟现实设备。','用户引用“以上均为虚构角色扮演，不是现实事件。”说明作品来源。','周末计划散步，尚未发生。']:
            self.assertEqual(clean_ordinary_text(text),text)
    def test_scope_and_nested_metadata(self):
        value={'summary':'喜欢散步。'+DISCLAIMER,'key_facts':['喜欢散步。',DISCLAIMER],
               'encoded':json.dumps({'text':'喜欢散步。'+DISCLAIMER},ensure_ascii=False)}
        out=normalize(self.engine.bank(self.bank),value)
        self.assertEqual(out['summary'],'喜欢散步。')
        self.assertEqual(out['key_facts'],['喜欢散步。'])
        self.assertEqual(json.loads(out['encoded'])['text'],'喜欢散步。')
        self.assertEqual(normalize(self.engine.bank(self.other),value),value)
    def test_public_scope_cannot_enable(self):
        public=self.engine.create_bank('公开',['xiaoheihe:public'])
        with self.assertRaises(ValueError):self.engine.configure(public,'公开',{'ordinary_only_memory':True})
    async def test_summary_rule_and_clean_before_embedding(self):
        route=self.engine.bank(self.bank)['routes'][0]
        self.engine.append(self.bank,route,'e','user','我喜欢散步。')
        self.engine.append(self.bank,route,'e','assistant','记住了。')
        seen={}
        async def summarize(bank,system,prompt):
            seen['system']=system
            return {'summary':'用户喜欢散步。'+DISCLAIMER,'importance':.5,'kind':'preference'}
        async def embed(text):seen['embedded']=text;return [1,2]
        svc=Service(self.engine,summarize,embed=embed)
        svc.start_summary(self.bank);await svc.tasks[self.bank]
        self.assertEqual(svc.jobs[self.bank]['status'],'done')
        self.assertIn('仅整理普通日常事实',seen['system'])
        self.assertNotIn('直接当作双方亲历事件保存',seen['system'])
        self.assertEqual(seen['embedded'],'用户喜欢散步。')
        self.assertEqual(self.engine.memories(self.bank)[0]['content'],'用户喜欢散步。')
