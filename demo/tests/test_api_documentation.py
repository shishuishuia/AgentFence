"""Compare documented response shapes with actual Rust (not a full schema engine)."""

import json
from pathlib import Path
import re
import tempfile
import unittest

from demo.fixture import prepare_fixture
from demo.runtime import ManagedBroker, REPO, default_binary
from integrations import AdminClient, AgentClient, BrokerHttp


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((REPO / "docs/openapi.json").read_text(encoding="utf-8"))

    def matches(self, schema, value):
        if "$ref" in schema:
            ref = self.spec
            for key in schema["$ref"][2:].split("/"):
                ref = ref[key]
            return self.matches(ref, value)
        for alternatives in ("oneOf", "anyOf"):
            if alternatives in schema:
                successes = 0
                for candidate in schema[alternatives]:
                    try:
                        self.matches(candidate, value)
                        successes += 1
                    except AssertionError:
                        pass
                self.assertTrue(successes == 1 if alternatives == "oneOf" else successes > 0)
                return
        for part in schema.get("allOf", []):
            self.matches(part, value)
        kind = schema.get("type")
        types = {"object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list), "integer": lambda x: type(x) is int, "number": lambda x: type(x) in (int, float), "string": lambda x: isinstance(x, str), "boolean": lambda x: type(x) is bool, "null": lambda x: x is None}
        if kind:
            self.assertTrue(types[kind](value), f"expected {kind}")
        if "enum" in schema:
            self.assertIn(value, schema["enum"])
        if "const" in schema:
            self.assertEqual(value, schema["const"])
        if isinstance(value, dict):
            self.assertTrue(set(schema.get("required", [])) <= value.keys(), "missing documented required field")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                self.assertTrue(value.keys() <= properties.keys(), "unexpected field absent from documentation")
            for key, item in value.items():
                if key in properties:
                    self.matches(properties[key], item)
        elif isinstance(value, list) and "items" in schema:
            for item in value:
                self.matches(schema["items"], item)
        elif isinstance(value, str) and "pattern" in schema:
            self.assertIsNotNone(re.search(schema["pattern"], value))

    def test_local_refs_resolve(self):
        def walk(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    ref = self.spec
                    for key in value["$ref"][2:].split("/"):
                        ref = ref[key]
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(self.spec)

    @unittest.skipUnless(default_binary().is_file(), "build release Rust before documentation checks")
    def test_representative_real_responses(self):
        schemas = self.spec["components"]["schemas"]
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "fixture"
            prepare_fixture(fixture)
            token = "documentation-test-" + "a" * 32
            with ManagedBroker(default_binary(), fixture, token) as broker:
                http = BrokerHttp(broker.base_url)
                admin = AdminClient(http, token)
                self.matches(schemas["HealthResponse"], http.health().require(200))
                created = admin.create_task("demo").require(201)
                self.matches(schemas["CreateTaskResponse"], created)
                task = created["task_id"]
                agent = AgentClient(http, task, created["agent_token"])
                self.matches(schemas["ToolResponse"], agent.read_file("src/main.rs").require(200))
                self.matches(schemas["PhaseChangeResponse"], admin.advance(task, "edit").require(200))
                grant = admin.issue_grant(task, "src/main.rs").require(201)
                self.matches(schemas["GrantResponse"], grant)
                self.matches(schemas["ToolResponse"], agent.write_file("src/main.rs", "test\n", grant_id=grant["grant_id"]).require(200))
                self.matches(schemas["GrantResponse"], admin.revoke_grant(task, grant["grant_id"]).require(200))
                self.matches(schemas["TaskListResponse"], admin.tasks().require(200))
                self.matches(schemas["TaskDetailResponse"], admin.task(task).require(200))
                self.matches(schemas["EventListResponse"], admin.events().require(200))
                failure = agent.write_file("src/main.rs", "must fail\n", grant_id=grant["grant_id"]).payload
                self.matches(schemas["ToolResponse"], failure)
                self.matches(schemas["ErrorResponse"], failure)
