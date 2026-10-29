"""Opt-in smoke test against a separately installed, pinned official Harness.

Set AGENTFENCE_DSH_ENTRY to its lib/bin.js. This test makes an isolated
profile and never calls a model or uses a user project or API credential.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest


ENTRY = os.environ.get("AGENTFENCE_DSH_ENTRY")
NODE = shutil.which("node")


@unittest.skipUnless(os.name == "nt" and ENTRY and NODE, "requires an explicitly configured Windows Harness installation")
class InstalledHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_guard_denies_native_read_and_shell(self):
        entry = Path(ENTRY).resolve(strict=True)
        manifest = json.loads((entry.parent.parent / "package.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest.get("name"), manifest.get("version")), ("@deepseek-ai/dsh", "0.2.1-rc.2"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
            env["DSH_HOME"] = str(root / "home")
            launch = [NODE, str(entry), "--profile", "acp"]
            init = await asyncio.create_subprocess_exec(*launch, "--help", cwd=root, env=env,
                                                        stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.DEVNULL)
            self.assertEqual(await asyncio.wait_for(init.wait(), 30), 0)
            profile = root / "home" / "profiles" / "acp"
            plugin = profile / "node_modules" / "@agentfence" / "readonly-guard"
            plugin.mkdir(parents=True)
            (plugin / "package.json").write_text('{"name":"@agentfence/readonly-guard","type":"module","main":"index.js"}', encoding="utf-8")
            marker = root / "guard-probe.json"
            source = '''import { writeFileSync } from "node:fs";
export const name = "agentfence-readonly-guard";
export const inject = ["tools"];
export function apply(ctx) {
  const allow = new Set(["mcp__agentfence__agentfence_list_files", "mcp__agentfence__agentfence_read_file", "mcp__agentfence__agentfence_task_status"]);
  ctx.tools.guard(exec => allow.has(exec.name) ? undefined : "AgentFence blocked this tool");
  setTimeout(async () => {
    const checks = {};
    for (const [name, args] of [["read", {file_path:"missing-probe-file"}], ["pwsh", {command:"Write-Output probe",description:"Run harmless temporary guard probe"}]]) {
      const present = !!ctx.tools.get(name);
      const result = present ? await ctx.tools.execute({name, callId:"probe-"+name, arguments:args, signal:AbortSignal.timeout(5000)}) : null;
      checks[name] = {present, denied: !!result && JSON.stringify(result).includes("AgentFence blocked this tool")};
    }
    writeFileSync(MARKER, JSON.stringify(checks));
  }, 2500);
}'''.replace("MARKER", json.dumps(str(marker)))
            (plugin / "index.js").write_text(source, encoding="utf-8")
            (profile / "cordis.patch.yml").write_text("- insert:\n    - id: agentfence-readonly-guard\n      name: '@agentfence/readonly-guard'\n", encoding="utf-8")
            process = await asyncio.create_subprocess_exec(*launch, cwd=root, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            try:
                request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1, "clientCapabilities": {}}}
                process.stdin.write((json.dumps(request) + "\n").encode())
                await process.stdin.drain()
                response = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
                self.assertEqual(response.get("result", {}).get("protocolVersion"), 1)
                for _ in range(120):
                    if marker.is_file():
                        break
                    await asyncio.sleep(0.1)
                checks = json.loads(marker.read_text(encoding="utf-8"))
                self.assertEqual(checks, {"read": {"present": True, "denied": True}, "pwsh": {"present": True, "denied": True}})
            finally:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
