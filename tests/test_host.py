import json
import unittest
from types import SimpleNamespace as NS
from astrbot_plugin_memory_branches.service import HostHistory


class Conversations:
    def __init__(self):
        self.records={
            'a':NS(cid='a',user_id='qq_bot:FriendMessage:1',history=json.dumps([{'role':'user','content':'原始'}]),title='A',persona_id='one',token_usage=3),
            'b':NS(cid='b',user_id='qq_bot:FriendMessage:2',history='[]',title='B',persona_id='two',token_usage=0)}
        self.current={'qq_bot:FriendMessage:1':'a','qq_bot:FriendMessage:2':'b'}
    async def get_conversations(self,unified_msg_origin=None):return [r for r in self.records.values() if not unified_msg_origin or r.user_id==unified_msg_origin]
    async def get_curr_conversation_id(self,origin):return self.current.get(origin)
    async def update_conversation(self,origin,cid,**fields):
        self.assert_owner(origin,cid)
        for k,v in fields.items():setattr(self.records[cid],'history' if k=='history' else k,json.dumps(v) if k=='history' else v)
    async def switch_conversation(self,origin,cid):self.current[origin]=cid
    async def delete_conversation(self,origin,cid):self.assert_owner(origin,cid);del self.records[cid]
    def assert_owner(self,origin,cid):
        if self.records[cid].user_id!=origin:raise AssertionError('cross-bank access')


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_host_history_and_new_conversations_rollback_without_other_user(self):
        manager=Conversations();host=HostHistory(NS(conversation_manager=manager));route='qq_bot:FriendMessage:1'
        snapshot=await host.snapshot_routes([route]);other=manager.records['b'].history
        manager.records['a'].history=json.dumps([{'role':'user','content':'新增'}])
        manager.records['c']=NS(cid='c',user_id=route,history='[]',title='new',persona_id=None,token_usage=0)
        await host.restore(snapshot,[route])
        self.assertEqual(json.loads(manager.records['a'].history)[0]['content'],'原始')
        self.assertNotIn('c',manager.records);self.assertEqual(manager.records['b'].history,other)

    async def test_mismatched_host_snapshot_rejected(self):
        host=HostHistory(NS(conversation_manager=Conversations()))
        with self.assertRaises(PermissionError):await host.restore({'qq_bot:FriendMessage:2':{}},['qq_bot:FriendMessage:1'])

    async def test_external_host_deletion_fails_instead_of_partial_fabrication(self):
        manager=Conversations();host=HostHistory(NS(conversation_manager=manager));route='qq_bot:FriendMessage:1'
        snapshot=await host.snapshot_routes([route]);del manager.records['a']
        with self.assertRaises(ValueError):await host.restore(snapshot,[route])
