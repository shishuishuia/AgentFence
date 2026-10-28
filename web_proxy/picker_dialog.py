"""Native chooser process. stdout carries one private result to the local helper."""

import json


def main():
    # Tk must own the main thread; never run a dialog on an HTTP worker thread.
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title="AgentFence：选择只读审查项目", mustexist=True)
        print(json.dumps({"path": selected or None}), flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
