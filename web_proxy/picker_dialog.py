"""Native chooser process. stdout carries one private result to the local helper."""

import json


def _focus_window():
    """Bring this process's visible window to the foreground.

    A Tk dialog started from a background child can open behind the browser.
    Enumerating this process's windows avoids guessing which HWND to raise.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        pid = kernel32.GetCurrentProcessId()
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def collect(hwnd, _):
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(collect, 0)
        for hwnd in found:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def main():
    # Tk must own the main thread; never run a dialog on an HTTP worker thread.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()
    try:
        root.after(300, _focus_window)
        selected = filedialog.askdirectory(
            parent=root,
            title="AgentFence：选择只读审查项目",
            mustexist=True,
        )
        print(json.dumps({"path": selected or None}), flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
