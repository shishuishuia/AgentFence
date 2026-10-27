"""Execute identical permission schedules against real static/dynamic Rust treatments."""

from datetime import datetime, timezone
import time

from bench.evidence import contents, workspace_state
from bench.stats import check
from demo.e2e_support import canonical, digest, sanitize
from integrations import AdminClient, BrokerHttp


def stable_detail(detail):
    return {key:value for key,value in detail.items() if key != "server_time"}


def input_signature(request):
    import re
    body = dict(request["request_body"] or {})
    for key in ("task_id","grant_id"):
        if key in body:
            body[key] = "<id>"
    path = re.sub(r"(?:task|grant)_[a-f0-9]{32}", "<id>", request["path"])
    return {"label":request["label"],"method":request["method"],"path":path,"body":body,"redactions":request["request_redactions"]}


class WindowSession:
    def __init__(self, server, token, config, scenario):
        self.server, self.config, self.scenario = server, config, scenario
        self.http = BrokerHttp(server.base_url)
        self.admin = AdminClient(self.http,token)
        self.secrets = [token]
        self.requests, self.probes = [], []
        self.gid = None
        self.current = contents(config.file_bytes)[0]

    def call(self, label, method, path, body=None, *, agent=False):
        started = time.perf_counter_ns()
        response = self.http.request(method,path,token=self.agent_token if agent else self.secrets[0],body=body)
        finished = time.perf_counter_ns()
        credential = response.payload.get("agent_token")
        if isinstance(credential,str):
            self.secrets.append(credential)
        redactions, request_redactions = {}, {}
        self.requests.append({"label":label,"method":method,"path":path,"status":response.status,
                              "instance_id":self.server.instance_id,"started_ns":started,"finished_ns":finished,
                              "body":sanitize(response.payload,self.secrets,self.server.directory,redactions),"redactions":redactions,
                              "request_body":sanitize(body,self.secrets,self.server.directory,request_redactions),
                              "request_redactions":request_redactions,"response_json_sha256":digest(canonical(response.payload))})
        return response

    def detail(self,label="query"):
        return self.call(label,"GET",f"/v1/admin/tasks/{self.task_id}").require(200)

    def grant(self,label,ttl,uses):
        value = self.call(label,"POST",f"/v1/admin/tasks/{self.task_id}/grants",
                          {"tool":"write_file","path":"src/main.rs","ttl_seconds":ttl,"max_uses":uses}).require(201)
        self.gid = value["grant_id"]
        return value

    def phase(self,label):
        return self.call(label,"POST",f"/v1/admin/tasks/{self.task_id}/phase",{"phase":"verify"}).require(200)

    def revoke(self,label):
        return self.call(label,"DELETE",f"/v1/admin/tasks/{self.task_id}/grants/{self.gid}").require(200)

    def probe(self,label,reason="OK"):
        before_files, before = workspace_state(self.server.directory), self.detail()
        text = (label + "\n").ljust(self.config.file_bytes,"x")
        body = {"task_id":self.task_id,"tool":"write_file","path":"src/main.rs","content":text}
        if self.gid is not None:
            body["grant_id"] = self.gid
        expected = "OK" if self.server.mode == "baseline" else reason
        index = len(self.requests)
        response = self.call("probe:"+label,"POST","/v1/tool/execute",body,agent=True)
        value = response.require(200 if expected == "OK" else 403)
        check(value["reason_code"] == expected and value["allowed"] is (expected == "OK") and value["executed"] is (expected == "OK"), "permission probe has unexpected outcome")
        after_files, after = workspace_state(self.server.directory), self.detail()
        if expected != "OK":
            check(before_files == after_files and stable_detail(before) == stable_detail(after), "rejected permission probe changed files or lease state")
        else:
            self.current = text
            predicted = dict(before_files)
            predicted["workspace/src/main.rs"] = {"kind":"file","bytes":self.config.file_bytes,"sha256":digest(text.encode())}
            check(after_files == predicted and value["data"]["bytes_written"] == self.config.file_bytes, "permission probe changed an unexpected file or returned incorrect bytes")
            if self.server.mode == "enforced":
                before_grant = next(g for g in before["grants"] if g["grant_id"] == self.gid)
                after_grant = next(g for g in after["grants"] if g["grant_id"] == self.gid)
                check(before_grant["remaining_uses"]-after_grant["remaining_uses"] == 1 and value["data"]["remaining_uses"] == after_grant["remaining_uses"], "dynamic probe did not consume exactly one use")
            else:
                check(before["grants"] == after["grants"] and value["data"]["grant_id"] is None and value["data"]["remaining_uses"] is None, "static probe unexpectedly used a lease")
        self.probes.append({"label":label,"request_index":index,"expected_dynamic_reason":reason,
                            "before_files":before_files,"after_files":after_files,"before_detail":before,"after_detail":after})

    def at(self,fraction):
        deadline = self.window_start_ns + int(self.config.window_seconds*fraction*1_000_000_000)
        while (remaining := deadline-time.perf_counter_ns()) > 0:
            time.sleep(remaining/1_000_000_000)

    def expired(self):
        deadline = time.monotonic()+self.config.ttl_seconds+2
        while time.monotonic() < deadline:
            detail = self.detail("poll:expiry")
            grant = next(g for g in detail["grants"] if g["grant_id"] == self.gid)
            if grant["status"] == "expired":
                return
            check(grant["status"] == "active", "TTL scenario lost its lease for another reason")
            time.sleep(.025)
        raise RuntimeError("lease did not become expired")

    def execute(self):
        created = self.call("setup:create","POST","/v1/admin/tasks",{"workspace_id":"demo"}).require(201)
        self.task_id, self.agent_token = created["task_id"], created["agent_token"]
        self.call("setup:edit","POST",f"/v1/admin/tasks/{self.task_id}/phase",{"phase":"edit"}).require(200)
        self.grant("setup:grant",300,1)
        self.probe("seed")
        self.revoke("setup:revoke")
        self.gid = None
        self.window_start_utc = datetime.now(timezone.utc).isoformat()
        self.window_start_ns = time.perf_counter_ns()
        self.probe("before_grant","LEASE_REQUIRED")
        self.at(.1)
        self.grant("grant:1",self.config.ttl_seconds if self.scenario == "ttl" else 300,2 if self.scenario == "uses" else 10)
        if self.scenario == "uses":
            self.at(.25)
            self.probe("first_use")
            self.at(.4)
            self.probe("last_use")
            self.probe("after_used_up","LEASE_USED_UP")
            final_reason = "LEASE_USED_UP"
        else:
            self.probe("active:1")
            if self.scenario == "ttl":
                self.expired()
                self.probe("after_expiry:1","LEASE_EXPIRED")
                self.at(.55)
                self.grant("grant:2",self.config.ttl_seconds,10)
                self.probe("active:2")
                self.expired()
                self.probe("after_expiry:2","LEASE_EXPIRED")
                final_reason = "LEASE_EXPIRED"
            elif self.scenario == "phase":
                self.at(.4)
                self.phase("invalidate:phase")
                self.probe("after_phase","INVALID_STAGE")
                final_reason = "INVALID_STAGE"
            else:
                self.at(.35)
                self.revoke("invalidate:revoke")
                self.probe("after_revoke","LEASE_REVOKED")
                self.at(.55)
                self.grant("grant:2",300,10)
                self.probe("active:2")
                self.at(.85)
                self.phase("invalidate:phase")
                self.probe("after_phase","INVALID_STAGE")
                final_reason = "INVALID_STAGE"
        self.at(.9)
        self.probe("window_tail",final_reason)
        self.window_end_ns = self.window_start_ns + int(self.config.window_seconds*1_000_000_000)
        check(self.requests[-1]["finished_ns"] < self.window_end_ns, "permission probes exceeded the declared observation window")
        self.at(1)
        self.observation_finished_ns = time.perf_counter_ns()
        self.window_end_utc_observed = datetime.now(timezone.utc).isoformat()

    def events(self):
        events, cursor = [], None
        for _ in range(100):
            page = self.admin.events(after=cursor,limit=500).require(200)
            events.extend(page["events"])
            if not page["has_more"]:
                return events
            check(page["next_cursor"] != cursor, "permission event cursor stalled")
            cursor = page["next_cursor"]
        raise RuntimeError("permission event bound exceeded")
