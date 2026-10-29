"""Byte-only MCP stdio adapter for a user-restricted AgentFence pipe pair."""
import os
import re
import sys
import threading

def main():
    if os.name != 'nt' or len(sys.argv) != 2:
        return 2
    import win32con
    import win32event
    import win32file
    import win32pipe
    name = sys.argv[1]
    if not re.fullmatch(r'\\\\\.\\pipe\\agentfence-[a-f0-9]{48}', name):
        return 2
    inbound = outbound = mutex = None
    acquired = False
    try:
        mutex = win32event.OpenMutex(win32con.SYNCHRONIZE | 0x0001, False,  # MUTEX_MODIFY_STATE
                                     'Local\\agentfence-' + name.rsplit('-', 1)[-1])
        wait = win32event.WaitForSingleObject(mutex, 30000)
        if wait not in (win32con.WAIT_OBJECT_0, win32con.WAIT_ABANDONED):
            raise RuntimeError('pipe session lock unavailable')
        acquired = True
        win32pipe.WaitNamedPipe(name + '-in', 5000)
        win32pipe.WaitNamedPipe(name + '-out', 5000)
        inbound = win32file.CreateFile(name + '-in', win32con.GENERIC_WRITE, 0, None, win32con.OPEN_EXISTING, 0, None)
        outbound = win32file.CreateFile(name + '-out', win32con.GENERIC_READ, 0, None, win32con.OPEN_EXISTING, 0, None)
    except Exception:
        if inbound is not None:
            inbound.Close()
        if acquired:
            win32event.ReleaseMutex(mutex)
        if mutex is not None:
            mutex.Close()
        return 1
    def forward_input():
        try:
            while True:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    break
                win32file.WriteFile(inbound, data)
        except Exception:
            pass
        finally:
            try:
                inbound.Close()
            except Exception:
                pass
    thread = threading.Thread(target=forward_input, daemon=True)
    thread.start()
    try:
        while True:
            _, data = win32file.ReadFile(outbound, 65536)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except Exception:
        pass
    finally:
        try:
            outbound.Close()
        except Exception:
            pass
        if acquired:
            win32event.ReleaseMutex(mutex)
        if mutex is not None:
            mutex.Close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
