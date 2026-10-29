# Escape Demo

A small project used to demonstrate that AgentFence denies a read outside the
imported snapshot.

- `app.py` is a normal file.
- `notes.md` is a normal file.
- `secrets.txt` is larger than the 64 KiB per-file import limit, so it is
  excluded from the snapshot and is not visible to the Agent.
