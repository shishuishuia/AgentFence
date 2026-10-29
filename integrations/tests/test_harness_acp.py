"""ACP transport tests use a disposable fake process, never a real agent or API."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

from integrations.harness_acp import AcpError, AcpProcess

FAKE = r'''
import json, os, sys, time
mode = sys.argv[1]
marker = sys.argv[2]
secret = os.environ.get('FIXTURE_SECRET', '')
def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n'); sys.stdout.flush()
def receive():
    line = sys.stdin.readline()
    if not line: raise SystemExit(0)
    obj = json.loads(line)
    if secret and secret in line: raise SystemExit(7)
    return obj
first = receive()
if first['method'] != 'initialize' or first['params'].get('protocolVersion') != 1: raise SystemExit(8)
if mode == 'invalid_json':
    sys.stdout.write('not json\n'); sys.stdout.flush(); raise SystemExit(0)
if mode == 'truncated':
    sys.stdout.write('{"jsonrpc":'); sys.stdout.flush(); raise SystemExit(0)
if mode == 'rpc_error':
    send({'jsonrpc':'2.0', 'id':first['id'], 'error':{'code':-1, 'message':'PRIVATE_FAKE_ERROR'}}); raise SystemExit(0)
if mode == 'bad_version':
    send({'jsonrpc':'2.0', 'id':first['id'], 'result':{'protocolVersion':999}}); raise SystemExit(0)
send({'jsonrpc':'2.0','id':first['id'],'result':{'protocolVersion':1}})
second = receive()
if second['method'] != 'session/new' or second['params'].get('mcpServers') is None: raise SystemExit(9)
for server in second['params']['mcpServers']:
    if server.get('env') != []: raise SystemExit(10)
if mode in ('early_good', 'early_wrong', 'early_many'):
    sid = 'other' if mode == 'early_wrong' else 'session_test'
    for _ in range(33 if mode == 'early_many' else 1):
        send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':sid,'update':{'sessionUpdate':'config_option_update'}}})
send({'jsonrpc':'2.0','id':second['id'],'result':{'sessionId':'session_test'}})
third = receive()
if third['method'] != 'session/prompt' or third['params'].get('sessionId') != 'session_test': raise SystemExit(11)
if mode == 'hang':
    time.sleep(60); raise SystemExit(0)
if mode == 'client_request':
    send({'jsonrpc':'2.0','id':99,'method':'fs/read_text_file','params':{'path':'SENSITIVE_FILE'}})
    answer = receive()
    if answer.get('id') == 99 and answer.get('error', {}).get('code') == -32601:
        with open(marker, 'w', encoding='utf-8') as f: f.write('denied')
    raise SystemExit(0)
if mode == 'wrong_session':
    send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'other','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'wrong'}}}}); raise SystemExit(0)
if mode in ('permission_reject', 'permission_cancel', 'permission_ambiguous', 'permission_wrong', 'permission_malformed'):
    options = [{'kind':'allow_once','optionId':'approve'}, {'kind':'reject_once','optionId':'decline'}]
    if mode == 'permission_cancel': options = [{'kind':'allow_once','optionId':'approve'}]
    if mode == 'permission_ambiguous': options = [{'kind':'allow_once','optionId':'shared'}, {'kind':'reject_once','optionId':'shared'}]
    if mode == 'permission_malformed': options = 'invalid'
    send({'jsonrpc':'2.0','id':101,'method':'session/request_permission','params':{'sessionId':'other' if mode == 'permission_wrong' else 'session_test','options':options}})
    answer = receive()
    expected = {'outcome':'cancelled'} if mode in ('permission_cancel','permission_ambiguous') else {'outcome':'selected','optionId':'decline'}
    if answer.get('id') != 101 or answer.get('result',{}).get('outcome') != expected: raise SystemExit(12)
    with open(marker,'w',encoding='utf8') as f: f.write('permission denied')
def chunk(text):
    send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'session_test','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':text}}}})
if mode == 'leak': chunk(secret)
elif mode == 'leak_split': chunk(secret[:len(secret)//2]); chunk(secret[len(secret)//2:])
elif mode == 'long': chunk('x' * 20001)
elif mode != 'empty':
    send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'session_test','update':{'sessionUpdate':'tool_call','title':'read fixture'}}})
    chunk('Hello '); chunk('fixture')
stop = 'cancelled' if mode == 'cancelled' else 'end_turn'
send({'jsonrpc':'2.0','id':third['id'],'result':{'stopReason':stop}})
'''

class TrackedProcess(AcpProcess):
    """Capture the owned subprocess handle to verify it was reaped."""
    async def close(self):
        self.owned = self.process
        await super().close()


class AcpProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / 'fixture_acp.py'
        self.script.write_text(FAKE, encoding='utf8')
        self.secret = 'fixture-secret-never-real'
        self.progress = []
        self.env = {}
        seen = set()
        for k in ('SystemRoot', 'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP'):
            if k in os.environ and k.upper() not in seen:
                self.env[k] = os.environ[k]
                seen.add(k.upper())
        self.env['FIXTURE_SECRET'] = self.secret
        self.servers = [{'name':'agentfence','command':str(Path(sys.executable).resolve()),'args':[],'env':[]}]

    def tearDown(self):
        self.temp.cleanup()

    def make(self, mode='normal', timeout=8, args=None, servers=None):
        return TrackedProcess(Path(sys.executable), args or ['-u',str(self.script),mode,str(self.root/'denied')], self.root,
                              self.env, self.servers if servers is None else servers,
                              secrets=[self.secret], timeout=timeout, progress=self.progress.append)

    async def test_successful_session_is_bounded_and_child_is_reaped(self):
        process = self.make()
        result = await process.run('Review the synthetic fixture')
        self.assertEqual(result.text, 'Hello fixture')
        self.assertEqual(result.stop_reason, 'end_turn')
        self.assertIn({'progress_text':'正在连接外部 Agent'}, self.progress)
        self.assertIn({'progress_text':'正在执行任务'}, self.progress)
        self.assertIsNone(process.process)
        self.assertIsNotNone(process.owned.returncode)

    async def test_client_file_requests_are_denied_and_terminate_run(self):
        process = self.make('client_request')
        with self.assertRaises(AcpError):
            await process.run('Inspect test fixture')
        self.assertEqual((self.root/'denied').read_text(encoding='utf8'), 'denied')
        self.assertIsNotNone(process.owned.returncode)

    async def test_session_creation_early_updates_are_bound_and_bounded(self):
        valid = self.make('early_good')
        self.assertEqual((await valid.run('Inspect test fixture')).text, 'Hello fixture')
        self.assertIsNotNone(valid.owned.returncode)
        for mode in ('early_wrong', 'early_many'):
            with self.subTest(mode=mode):
                process = self.make(mode)
                with self.assertRaises(AcpError):
                    await process.run('Inspect test fixture')
                self.assertIsNotNone(process.owned.returncode)

    async def test_permission_requests_never_approve_and_bad_session_fails(self):
        for mode in ('permission_reject', 'permission_cancel', 'permission_ambiguous'):
            with self.subTest(mode=mode):
                marker = self.root/'denied'
                if marker.exists(): marker.unlink()
                process = self.make(mode)
                self.assertEqual((await process.run('Inspect test fixture')).text, 'Hello fixture')
                self.assertEqual(marker.read_text(encoding='utf8'), 'permission denied')
                self.assertIsNotNone(process.owned.returncode)
        for mode in ('permission_wrong', 'permission_malformed'):
            with self.subTest(mode=mode):
                process = self.make(mode)
                with self.assertRaises(AcpError):
                    await process.run('Inspect test fixture')
                self.assertIsNotNone(process.owned.returncode)

    async def test_bad_frames_errors_or_wrong_session_fail_without_raw_output(self):
        for mode in ('invalid_json', 'truncated', 'rpc_error', 'bad_version', 'wrong_session', 'empty', 'cancelled', 'long', 'leak'):
            with self.subTest(mode=mode):
                process = self.make(mode)
                with self.assertRaises(AcpError) as caught:
                    await process.run('Inspect test fixture')
                self.assertNotIn('PRIVATE_FAKE_ERROR', str(caught.exception))
                self.assertNotIn(self.secret, str(caught.exception))
                self.assertIsNotNone(process.owned.returncode)

    async def test_timeout_reaps_owned_subprocess(self):
        process = self.make('hang', timeout=1)
        with self.assertRaises(AcpError):
            await process.run('Inspect test fixture')
        self.assertIsNotNone(process.owned.returncode)

    async def test_cancellation_reaps_owned_subprocess(self):
        event = asyncio.Event()
        process = self.make('hang')
        process.progress = lambda value: event.set() if value.get('progress_text') == '正在执行任务' else None
        task = asyncio.create_task(process.run('Inspect test fixture'))
        await asyncio.wait_for(event.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNotNone(process.owned.returncode)

    async def test_credential_never_serialized_into_goal_or_mcp_declaration(self):
        process = self.make()
        with self.assertRaises(AcpError):
            await process.run('Goal includes ' + self.secret)
        self.assertIsNone(process.process)
        with self.assertRaises(AcpError):
            self.make(servers=[{'name':'x','command':str(Path(sys.executable).resolve()),'args':[],'env':[{'name':'TOKEN','value':self.secret}]}])
        with self.assertRaises(AcpError):
            self.make(servers=[{'name':'x','command':str(Path(sys.executable).resolve()),'args':[self.secret],'env':[]}])
        with self.assertRaises(AcpError):
            self.make(args=['-c', self.secret])
        forbidden_env = {**self.env, 'AGENTFENCE_ADMIN_TOKEN':'test-only-admin'}
        with self.assertRaises(AcpError):
            TrackedProcess(Path(sys.executable), ['-c','pass'], self.root, forbidden_env, [])

    async def test_split_credential_is_rejected_from_combined_report(self):
        process = self.make('leak_split')
        with self.assertRaises(AcpError):
            await process.run('Inspect test fixture')
        self.assertIsNotNone(process.owned.returncode)

if __name__ == '__main__':
    unittest.main()
