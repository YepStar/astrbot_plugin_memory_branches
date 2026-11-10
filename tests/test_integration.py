"""Integration checks against the AstrBot and Quart shipped in the local image."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from quart import Quart

# Skip on a standalone Python installation; engine tests remain independent.
try:
    from astrbot_plugin_memory_branches.main import MemoryBranchesPlugin
except ImportError:
    MemoryBranchesPlugin=None


@unittest.skipIf(MemoryBranchesPlugin is None,'AstrBot integration runtime unavailable')
class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_api_summary_and_rollback_are_connected(self):
        from astrbot_plugin_memory_branches.tests.test_host import Conversations
        manager=Conversations()
        class Context:
            conversation_manager=manager
            def register_web_api(self,*args):pass
            def get_registered_star(self,*args):return None
            async def llm_generate(self,**kwargs):return NS(completion_text=json.dumps({'summary':'用户喜欢音乐','importance':.7,'kind':'preference','topics':['音乐'],'key_facts':['喜欢音乐'],'participants':['用户']}))
        with tempfile.TemporaryDirectory() as root,patch('astrbot_plugin_memory_branches.main.StarTools.get_data_dir',return_value=Path(root)):
            plugin=MemoryBranchesPlugin(Context(),{'summary_provider_id':'test'})
            route='qq_bot:FriendMessage:1'
            bank=plugin.engine.create_bank('A',[route],await plugin.host.snapshot_routes([route]))
            await plugin.initialize()
            class Event:
                unified_msg_origin=route
                message_str='我喜欢音乐'
                message_obj=NS(message_id='msg1')
                extra={}
                def get_extra(self,key):return self.extra.get(key)
                def set_extra(self,key,value):self.extra[key]=value
                def get_result(self):return NS(get_plain_text=lambda:'最终可见回复')
                def stop_event(self):raise AssertionError('unexpected stop')
            event=Event();req=NS(prompt='我喜欢音乐',system_prompt='test')
            await plugin.recall_request(event,req)
            await plugin.capture_response(event,NS(role='assistant',tools_call_name=[],completion_text='模型初稿'))
            manager.records['a'].history=json.dumps([{'role':'user','content':'我喜欢音乐'},{'role':'assistant','content':'最终可见回复'}])
            await plugin.capture_sent(event)
            self.assertEqual(plugin.engine.pending(bank)[-1]['content'],'最终可见回复')
            app=Quart(__name__)
            async with app.test_request_context('/action',method='POST',json={'action':'summarize','bank':bank}):
                result=await plugin.api_action()
                self.assertEqual(result['status'],'ok')
            await plugin.service.tasks[bank]
            async with app.test_request_context('/state?bank='+bank):
                state=(await plugin.api_state())['data']['state']
            self.assertEqual(state['memory_count'],1)
            node=state['nodes'][0]['id']
            async with app.test_request_context('/action',method='POST',json={'action':'rollback','bank':bank,'node':node,'revision':state['revision']}):
                result=await plugin.api_action()
                self.assertEqual(result['status'],'ok')
            self.assertEqual(plugin.engine.state(bank)['memory_count'],0)
            self.assertEqual(json.loads(manager.records['a'].history)[0]['content'],'原始')
            await plugin.terminate()
