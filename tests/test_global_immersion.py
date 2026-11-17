import tempfile,unittest
from pathlib import Path
from astrbot_plugin_memory_branches.engine import Engine
from astrbot_plugin_memory_branches.scene_policy import enabled,normalize,IMMERSIVE
from astrbot_plugin_memory_branches.service import Service

TAIL='以上均为角色扮演创作，不是用户现实经历，也不代表真实人物关系。'

class GlobalImmersionTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'memory.db'
  legacy=Engine(self.path)
  self.private=legacy.create_bank('existing',['qq_bot:FriendMessage:1'])
  self.public=legacy.create_bank('public',['bilibili:public'])
  self.engine=Engine(self.path,immersive_memory=True)
 def tearDown(self):self.tmp.cleanup()
 def test_all_existing_and_future_banks_follow_global_without_reconfiguration(self):
  new=self.engine.create_bank('future',['qq_bot:FriendMessage:2'])
  for bank in [self.private,self.public,new]:
   info=self.engine.bank(bank)
   self.assertTrue(enabled(info))
   self.assertEqual(normalize(info,'普通记事。'+TAIL),'普通记事。')
 def test_disabling_global_overrides_old_per_bank_immersive_setting(self):
  self.engine.configure(self.private,'existing',{'scene_memory_policy':IMMERSIVE})
  self.engine.immersive_memory=False
  self.assertFalse(enabled(self.engine.bank(self.private)))
  self.assertEqual(normalize(self.engine.bank(self.private),TAIL),TAIL)
 def test_banks_keep_routes_and_memories_separate(self):
  self.engine.import_memory(self.private,'test','1','私聊中约好散步。'+TAIL)
  self.engine.import_memory(self.public,'test','2','B站作者分享自己的旅行。'+TAIL)
  self.assertEqual(self.engine.memories(self.private)[0]['content'],'私聊中约好散步。')
  self.assertEqual(self.engine.memories(self.public)[0]['content'],'B站作者分享自己的旅行。')
  self.assertEqual(self.engine.route('bilibili:public'),self.public)
 async def test_summarizer_uses_global_rule_for_existing_public_bank(self):
  self.engine.append(self.public,'bilibili:public','one','user','B站作者分享自己的旅行。')
  self.engine.append(self.public,'bilibili:public','one','assistant','保存作者动态。')
  seen={}
  async def summarize(bank,system,prompt):
   seen['system']=system
   return {'summary':'B站作者分享自己的旅行。'+TAIL,'importance':.5,'kind':'episodic'}
  svc=Service(self.engine,summarize)
  svc.start_summary(self.public);await svc.tasks[self.public]
  self.assertIn('本插件统一采用沉浸式记事口径',seen['system'])
  self.assertIn('外部资料',seen['system'])
  self.assertEqual(self.engine.memories(self.public)[0]['content'],'B站作者分享自己的旅行。')
