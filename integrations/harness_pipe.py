"""Windows current-user-restricted relay to the task-bound Rust MCP process.

The external MCP client receives pipe names, never task credentials. Each
connection owns a fresh Rust child to support client probing and reconnects.
The authenticated OS user is the trust boundary; this is not a same-user sandbox.
"""
from __future__ import annotations
import os
from pathlib import Path
import secrets
import subprocess
import threading

from integrations.mcp_review import child_environment

class PipeRelayError(RuntimeError):
    """Safe failure without file paths, pipe names or credentials."""

class TaskPipeRelay:
    """Own bounded, current-user-only pipe pairs and task-scoped MCP children."""
    def __init__(self, binary, broker_url, task_id, task_token, *, max_connections=12):
        if os.name != 'nt':
            raise PipeRelayError('当前任务中继仅支持原生 Windows。')
        if type(max_connections) is not int or not 1 <= max_connections <= 32:
            raise ValueError('invalid connection budget')
        binary = Path(binary).resolve(strict=True)
        if not binary.is_file():
            raise PipeRelayError('MCP 程序不可用。')
        self._binary = str(binary)
        self._env = child_environment(broker_url, task_id, task_token)
        self.name = r'\\.\pipe\agentfence-' + secrets.token_hex(24)
        self._limit = max_connections
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._workers = set()
        self._children = set()
        self._pipes = set()
        self._failure = None
        self._thread = None
        self._mutex = None

    @property
    def mutex_name(self):
        """Name of the per-task lock shared by cooperating pipe clients."""
        return 'Local\\agentfence-' + self.name.rsplit('-', 1)[-1]

    @staticmethod
    def _security(access=None):
        import pywintypes
        import win32api
        import win32con
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        acl = win32security.ACL()
        if access is None:
            access = win32con.GENERIC_READ | win32con.GENERIC_WRITE
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, access, sid)
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorDacl(1, acl, 0)
        attrs = pywintypes.SECURITY_ATTRIBUTES()
        attrs.SECURITY_DESCRIPTOR = descriptor
        attrs.bInheritHandle = False
        return attrs

    def start(self):
        if self._thread is not None:
            raise PipeRelayError('任务中继已启动。')
        try:
            attrs = self._security()
            import win32event
            # Serialize the two half-connections as one logical session. A
            # process exit releases the mutex even when the client crashes.
            import win32con
            mutex_attrs = self._security(win32con.SYNCHRONIZE | 0x0001)
            self._mutex = win32event.CreateMutex(mutex_attrs, False, self.mutex_name)
        except Exception:
            raise PipeRelayError('无法建立用户限制的管道安全描述符。') from None
        self._thread = threading.Thread(target=self._accept, args=(attrs,), daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or self._failure is not None:
            self.close()
            raise PipeRelayError('任务中继无法启动。')
        return self

    def _accept(self, attrs):
        import pywintypes
        import win32pipe
        first = True
        try:
            for _ in range(self._limit):
                if self._closed.is_set():
                    break
                extra = win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0
                mode = win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS
                inbound = win32pipe.CreateNamedPipe(self.name + '-in', win32pipe.PIPE_ACCESS_INBOUND | extra, mode, 4, 65536, 65536, 0, attrs)
                try:
                    outbound = win32pipe.CreateNamedPipe(self.name + '-out', win32pipe.PIPE_ACCESS_OUTBOUND | extra, mode, 4, 65536, 65536, 0, attrs)
                except Exception:
                    inbound.Close()
                    raise
                first = False
                with self._lock:
                    self._pipes.update((inbound, outbound))
                self._ready.set()
                for pipe in (inbound, outbound):
                    try:
                        win32pipe.ConnectNamedPipe(pipe, None)
                    except pywintypes.error as error:
                        if error.winerror != 535:
                            raise
                if self._closed.is_set():
                    self._release(inbound, outbound)
                    break
                worker = threading.Thread(target=self._serve, args=(inbound, outbound), daemon=True)
                with self._lock:
                    self._workers.add(worker)
                worker.start()
        except Exception:
            self._failure = 'pipe_failure'
            self._ready.set()

    def _release(self, *pipes):
        import win32pipe
        for pipe in pipes:
            try:
                win32pipe.DisconnectNamedPipe(pipe)
            except Exception:
                pass
            try:
                pipe.Close()
            except Exception:
                pass
        with self._lock:
            for pipe in pipes:
                self._pipes.discard(pipe)

    def _serve(self, inbound, outbound):
        import win32file
        child = None
        output = None
        try:
            if self._closed.is_set():
                return
            child = subprocess.Popen([self._binary], env=self._env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW, bufsize=0)
            with self._lock:
                self._children.add(child)
            def copy_output():
                try:
                    while not self._closed.is_set():
                        chunk = os.read(child.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        win32file.WriteFile(outbound, chunk)
                except Exception:
                    pass
                finally:
                    self._release(outbound)
            output = threading.Thread(target=copy_output, daemon=True)
            output.start()
            while not self._closed.is_set():
                _, data = win32file.ReadFile(inbound, 65536)
                if not data:
                    break
                child.stdin.write(data)
                child.stdin.flush()
        except Exception:
            pass
        finally:
            self._release(inbound, outbound)
            if child is not None:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)
                if child.stdin:
                    child.stdin.close()
                if child.stdout:
                    child.stdout.close()
                with self._lock:
                    self._children.discard(child)
            if output is not None:
                output.join(2)
            with self._lock:
                self._workers.discard(threading.current_thread())

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        if self._thread is not None:
            # Wake the pending accept by connecting both directions. These carry
            # no traffic and are discarded before a Rust child is created.
            import win32con
            import win32file
            for suffix, access in (('-in', win32con.GENERIC_WRITE), ('-out', win32con.GENERIC_READ)):
                try:
                    handle = win32file.CreateFile(self.name + suffix, access, 0, None, win32con.OPEN_EXISTING, 0, None)
                    handle.Close()
                except Exception:
                    pass
            self._thread.join(6)
        with self._lock:
            children = list(self._children)
            workers = list(self._workers)
            pipes = list(self._pipes)
        self._release(*pipes)
        for child in children:
            if child.poll() is None:
                child.terminate()
        for worker in workers:
            worker.join(6)
        if self._thread is not None and self._thread.is_alive():
            raise PipeRelayError('任务中继未能停止。')
        if any(worker.is_alive() for worker in workers):
            raise PipeRelayError('任务中继连接未能停止。')
        if self._mutex is not None:
            self._mutex.Close()
            self._mutex = None

    def __enter__(self):
        return self.start()
    def __exit__(self, *_):
        self.close()
