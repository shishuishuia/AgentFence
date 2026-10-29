"""Full local management HTTP → MCP → Rust edit-copy/approval flow."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection

from demo.fixture import prepare_fixture
from demo.runtime import ManagedBroker
from integrations.mcp_review import ReviewClient
from web_proxy.config import Config
from web_proxy.server import create_server
from web_proxy.workspaces import WorkspaceBackend, WorkspaceManager

ROOT = Path(__file__).resolve().parents[2]
SUFFIX = '.exe' if os.name == 'nt' else ''
BIN = Path(os.getenv('AGENTFENCE_V2_BIN_DIR', ROOT / 'target/v2/release'))

@unittest.skipUnless(importlib.util.find_spec('mcp') and (BIN / ('agentfence-server' + SUFFIX)).is_file() and (BIN / ('agentfence-mcp-server' + SUFFIX)).is_file(), 'requires MCP SDK and Rust binaries')
class EditFlowTest(unittest.TestCase):
    def test_browser_management_flow_requires_preview_and_explicit_approval(self):
        class Model:
            rounds = 0
            def complete(self, messages, tools):
                self.rounds += 1
                if self.rounds == 1:
                    return {'role':'assistant','content':None,'tool_calls':[{'id':'edit_test','type':'function','function':{'name':'agentfence_write_file','arguments':json.dumps({'path':'main.py','content':'print(2)\n'})}}]}, {}
                return {'role':'assistant','content':'Updated isolated main.py.'}, {}
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as tmp:
            root = Path(tmp).resolve(); prepare_fixture(root / 'fixture')
            project = root / 'project'; project.mkdir()
            for name, content in [('main.py','print(1)\n')]:
                (project / name).write_bytes(content.encode())
            admin = secrets.token_hex(32)
            with ManagedBroker(BIN / ('agentfence-server'+SUFFIX), root / 'fixture', admin) as broker:
                config = Config(broker.base_url, admin, port=0, dist=None, timeout=10)
                backend = WorkspaceBackend(config)
                factory = lambda summary, token, progress: ReviewClient(BIN / ('agentfence-mcp-server'+SUFFIX), broker.base_url, summary, token, admin, model=Model(), progress=progress)
                manager = WorkspaceManager(backend, picker=lambda: str(project), review_factory=factory)
                server = create_server(config, manager=manager)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                origin = f'http://127.0.0.1:{server.server_port}'; cookie = ''; csrf = ''
                def request(path, body=None, csrf_override=None):
                    connection = HTTPConnection('127.0.0.1',server.server_port,timeout=20)
                    headers = {'Origin':origin,'Content-Type':'application/json','Cookie':cookie,'X-AgentFence-CSRF':csrf if csrf_override is None else csrf_override}
                    connection.request('GET' if body is None else 'POST','/api/admin'+path,None if body is None else json.dumps(body),headers)
                    response = connection.getresponse(); payload=json.loads(response.read()); headers=dict(response.getheaders()); connection.close()
                    return response.status, payload, headers
                try:
                    status, payload, headers = request('/session', {})
                    self.assertEqual(status,200); cookie=headers['Set-Cookie'].split(';')[0]; csrf=payload['csrf_token']
                    status, selection, _ = request('/picker/start', {})
                    self.assertEqual(status,202); sid=selection['selection_id']
                    for _ in range(100):
                        status, selection, _ = request('/picker/'+sid)
                        if selection['state'] != 'pending': break
                        time.sleep(.02)
                    self.assertEqual(selection['state'],'ready')
                    status, workspace, _ = request('/workspaces/confirm', {'selection_id':sid,'display_name':'test','profile':'readonly_snapshot'})
                    self.assertEqual(status,201)
                    status, task, _ = request('/tasks', {'workspace_id':workspace['workspace_id'],'goal':'Modify main.py','permission_profile':'controlled_edit'})
                    self.assertEqual(status,201); self.assertNotIn('agent_token',task)
                    self.assertNotEqual(task['workspace_id'],workspace['workspace_id']); tid=task['task_id']
                    self.assertEqual(request(f'/tasks/{tid}/review/start', {})[0],202)
                    for _ in range(200):
                        status, review, _ = request(f'/tasks/{tid}/review')
                        if review['state'] != 'running': break
                        time.sleep(.05)
                    self.assertEqual(review['state'],'completed',review)
                    self.assertEqual((project/'main.py').read_bytes(),b'print(1)\n')
                    status, changes, _ = request(f'/tasks/{tid}/changes'); self.assertEqual(status,200)
                    entry=changes['changes'][0]; self.assertEqual(entry['path'],'main.py')
                    status, preview, _ = request(f'/tasks/{tid}/change?path=main.py'); self.assertEqual(status,200)
                    self.assertEqual((preview['before'],preview['after']),('print(1)\n','print(2)\n'))
                    body={'path':'main.py','after_sha256':entry['after_sha256']}
                    self.assertEqual(request(f'/tasks/{tid}/apply',body,csrf_override='bad')[0],403)
                    self.assertEqual((project/'main.py').read_bytes(),b'print(1)\n')
                    self.assertEqual(request(f'/tasks/{tid}/apply',body)[0],200)
                    self.assertEqual((project/'main.py').read_bytes(),b'print(2)\n')
                finally:
                    server.shutdown(); server.server_close(); thread.join(5)

if __name__ == '__main__': unittest.main()
