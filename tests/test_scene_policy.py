import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from astrbot_plugin_memory_branches.engine import Engine,unpack_snapshot
from astrbot_plugin_memory_branches.service import Service
from astrbot_plugin_memory_branches.scene_policy import IMMERSIVE,normalize,clean_text

DISCLAIMER='这些亲密互动属于虚拟聊天，不应记为用户现实中的身体接触。'

def payload(text):
 return {'summary':text,'importance':.7,'kind':'episodic','topics':['亲密互动与角色扮演'],
         'participants':['用户','冷月'],'key_facts':['两人拥抱'],
         'atoms':[{'content':'两人拥抱','kind':'episodic','importance':.7}]}

class ScenePolicyTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.engine=Engine(Path(self.tmp.name)/'mem.db')
  self.private=self.engine.create_bank('冷月私聊',['qq_bot:FriendMessage:12'])
  self.other=self.engine.create_bank('其他私聊',['qq_bot:FriendMessage:13'])
  self.public=self.engine.create_bank('公开',['xiaoheihe:public'])
  self.engine.configure(self.private,'冷月私聊',{'scene_memory_policy':IMMERSIVE})
 def tearDown(self):self.tmp.cleanup()
 def pair(self,bank):
  route=self.engine.bank(bank)['routes'][0]
  self.engine.append(bank,route,'a','user','抱抱我')
  self.engine.append(bank,route,'a','assistant','抱抱，今天辛苦啦。')
 def test_clean_known_disclaimer_preserves_event(self):
  self.assertEqual(clean_text('两人拥抱。'+DISCLAIMER),'两人拥抱。')
  self.assertEqual(clean_text('亲密互动与角色扮演'),'亲密互动')
 def test_preserves_games_quotes_plans_external_attribution(self):
  for text in ['用户喜欢角色扮演游戏和虚拟现实设备。','用户说现实里没有这样的宝藏女孩。','周末计划见面，尚未发生。','小黑盒作者说自己想买手办，未确认与QQ用户有关。']:
   self.assertEqual(clean_text(text),text)
 def test_public_and_other_private_keep_original_policy(self):
  self.assertEqual(normalize(self.engine.bank(self.other),DISCLAIMER),DISCLAIMER)
  self.assertEqual(normalize(self.engine.bank(self.public),DISCLAIMER),DISCLAIMER)
  with self.assertRaises(ValueError):self.engine.configure(self.public,'公开',{'scene_memory_policy':IMMERSIVE})
 def test_recursive_metadata_and_encoded_graph_labels(self):
  data={'summary':'两人拥抱。'+DISCLAIMER,'topics':['亲密互动与角色扮演'],
        'encoded':json.dumps({'topic':'亲密互动与角色扮演'},ensure_ascii=False)}
  out=normalize(self.engine.bank(self.private),data)
  self.assertEqual(out['summary'],'两人拥抱。')
  self.assertEqual(json.loads(out['encoded'])['topic'],'亲密互动')
 def test_commit_enforces_policy_and_clears_obsolete_vector(self):
  self.pair(self.private)
  node=self.engine.commit(self.engine.plan(self.private,manual=True),payload('两人拥抱。'+DISCLAIMER),[1,2])
  mem=self.engine.memories(self.private)[0]
  self.assertEqual(mem['content'],'两人拥抱。');self.assertEqual(mem['vector'],[])
  self.assertEqual(mem['metadata']['topics'],['亲密互动'])
  self.assertIn('ttl_days',mem['metadata']['atoms'][0])
  with self.engine.connect() as db:
   snap=unpack_snapshot(db.execute('select snapshot from nodes where id=?',(node,)).fetchone()[0])
  self.assertNotIn('虚拟聊天',json.dumps(snap,ensure_ascii=False))
 def test_import_enforces_policy_and_keeps_source_idempotent(self):
  self.assertTrue(self.engine.import_memory(self.private,'legacy','x','两人拥抱。'+DISCLAIMER))
  self.assertFalse(self.engine.import_memory(self.private,'legacy','x','两人拥抱。'+DISCLAIMER))
  self.assertEqual(self.engine.memories(self.private)[0]['content'],'两人拥抱。')
 async def test_summarizer_receives_scoped_policy_before_embedding(self):
  self.pair(self.private);seen={}
  async def summarize(bank,system,prompt):
   seen['system']=system;return payload('两人拥抱。'+DISCLAIMER)
  async def embed(text):seen['embedded']=text;return [1,2]
  svc=Service(self.engine,summarize,embed=embed)
  svc.start_summary(self.private);await svc.tasks[self.private]
  self.assertEqual(svc.jobs[self.private]['status'],'done')
  self.assertIn('直接当作双方亲历事件保存',seen['system'])
  self.assertNotIn('不要把助手编写的故事记成用户现实经历',seen['system'])
  self.assertEqual(seen['embedded'],'两人拥抱。')
 async def test_other_bank_summary_rules_unchanged(self):
  self.pair(self.other);seen={}
  async def summarize(bank,system,prompt):seen['system']=system;return payload('普通摘要')
  svc=Service(self.engine,summarize);svc.start_summary(self.other);await svc.tasks[self.other]
  self.assertIn('不要把助手编写的故事记成用户现实经历',seen['system'])

if __name__=='__main__':unittest.main()
