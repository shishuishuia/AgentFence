"""Windows restricted-pipe relay tests using the real Rust MCP executable."""
import asyncio
import importlib.util
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

from demo.fixture import prepare_fixture
from demo.runtime import ManagedBroker
from integrations.harness_pipe import TaskPipeRelay
from web_proxy.config import Config
from web_proxy.workspaces import WorkspaceBackend

ROOT = Path(__file__).resolve().parents[2]
BIN = Path(os.getenv('AGENTFENCE_V2_BIN_DIR', ROOT / 'target/debug'))

@unittest.skipUnless(os.name == 'nt' and importlib.util.find_spec('win32pipe') and importlib.util.find_spec('mcp') and (BIN/'agentfence-server.exe').is_file() and (BIN/'agentfence-mcp-server.exe').is_file(), 'requires Windows, MCP and Rust binaries')
class RestrictedPipeTests(unittest.TestCase):
    def test_acl_has_only_current_user(self):
        import win32api
        import win32con
        import win32security
        attrs = TaskPipeRelay._security()
        acl = attrs.SECURITY_DESCRIPTOR.GetSecurityDescriptorDacl()
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        self.assertEqual(acl.GetAceCount(), 1)
        self.assertEqual(acl.GetAce(0)[2], sid)

    def test_probe_and_reconnect_reach_task_bound_mcp(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as tmp:
            root=Path(tmp).resolve(); prepare_fixture(root/'fixture')
            project=root/'project'; project.mkdir()
            (project/'main.py').write_bytes(b'print(1)\n')
            (project/'other.py').write_bytes(b'print(2)\n')
            secret=secrets.token_hex(32)
            with ManagedBroker(BIN/'agentfence-server.exe', root/'fixture',secret) as broker:
                backend=WorkspaceBackend(Config(broker.base_url,secret,dist=None))
                preview=backend.preview(str(project)); workspace=backend.confirm(preview['preview_id'],'test')
                task=backend.create_readonly_task(workspace['workspace_id'],'test')
                before=(project/'main.py').read_bytes()
                with TaskPipeRelay(BIN/'agentfence-mcp-server.exe',broker.base_url,task['task_id'],task['agent_token']) as relay:
                    env = {'SystemRoot': os.environ.get('SystemRoot', r'C:\Windows')}
                    params=StdioServerParameters(command=sys.executable,args=['-u',str((ROOT/'integrations/harness_pipe_client.py').resolve()),relay.name],env=env)
                    async def run():
                        async def read_file(path, expected):
                            with open(os.devnull,'w') as stderr:
                                async with stdio_client(params,errlog=stderr) as (read,write):
                                    async with ClientSession(read,write) as client:
                                        await client.initialize()
                                        tools=await client.list_tools()
                                        self.assertEqual({tool.name for tool in tools.tools},{'agentfence_list_files','agentfence_read_file','agentfence_task_status'})
                                        result=await client.call_tool('agentfence_read_file',{'path':path})
                                        self.assertFalse(result.isError)
                                        self.assertEqual(result.structuredContent['content'],expected)
                        await read_file('main.py', 'print(1)\n')
                        await read_file('main.py', 'print(1)\n')
                        # Two clients connecting concurrently must never cross
                        # the input and output halves of their sessions.
                        await asyncio.gather(read_file('main.py', 'print(1)\n'),
                                             read_file('other.py', 'print(2)\n'))
                    asyncio.run(asyncio.wait_for(run(), timeout=25))
                self.assertEqual((project/'main.py').read_bytes(),before)
                from integrations.client import AdminClient, BrokerHttp
                events=AdminClient(BrokerHttp(broker.base_url),secret).events(task_id=task['task_id'],limit=100).require(200)['events']
                self.assertEqual(sum(e['event_type']=='result' and e['tool']=='read_file' and e['executed'] for e in events),4)

if __name__ == '__main__': unittest.main()
