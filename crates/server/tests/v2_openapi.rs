//! The published contract must distinguish administrator and task credentials.

use serde_json::{Value, json};

#[test]
fn v2_openapi_uses_the_correct_role_for_each_new_endpoint() {
    let spec: Value = serde_json::from_str(include_str!("../../../docs/openapi.json")).unwrap();
    for path in ["/v1/agent/tasks/{id}", "/v1/agent/tasks/{id}/files"] {
        assert_eq!(
            spec["paths"][path]["get"]["security"],
            json!([{"AgentBearer": []}])
        );
    }
    for (path, method) in [
        ("/v1/admin/workspace-previews", "post"),
        ("/v1/admin/workspace-previews/{id}/excluded", "get"),
        ("/v1/admin/workspaces", "post"),
        ("/v1/admin/workspaces", "get"),
        ("/v1/admin/workspaces/{id}/edit-copy", "post"),
        ("/v1/admin/tasks/{id}/changes", "get"),
        ("/v1/admin/tasks/{id}/change", "get"),
        ("/v1/admin/tasks/{id}/apply", "post"),
    ] {
        assert_eq!(
            spec["paths"][path][method]["security"],
            json!([{"AdminBearer": []}])
        );
    }
    let request = &spec["components"]["schemas"]["CreateTaskRequest"]["properties"];
    assert!(request.get("permission_profile").is_some());
    let read = &spec["components"]["schemas"]["ReadResult"]["properties"];
    assert!(read.get("sha256").is_some());
    assert!(read.get("snapshot_id").is_some());
}
