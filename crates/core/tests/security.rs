//! Core security invariants, independent of HTTP.

use agentfence_core::{
    Grant, GrantSpec, GrantStatus, PathError, Phase, Policy, ReasonCode, TimePoint, Tool, ToolCall,
    resolve_existing, validate_relative,
};
use chrono::{TimeZone, Utc};
use std::{fs, time::Duration};

fn now(seconds: u64) -> TimePoint {
    TimePoint {
        monotonic: Duration::from_secs(seconds),
        utc: Utc.timestamp_opt(1_700_000_000, 0).unwrap(),
    }
}

fn grant() -> Grant {
    Grant::issue(
        GrantSpec {
            grant_id: "g1".into(),
            task_id: "t1".into(),
            path: "src/main.rs".into(),
            ttl_seconds: 60,
            max_uses: 1,
        },
        now(10),
    )
    .unwrap()
}

fn policy() -> Policy {
    Policy::new(vec!["src/main.rs".into(), "Cargo.toml".into()], 65_536).unwrap()
}
fn write<'a>(path: &'a str, grant_id: Option<&'a str>) -> ToolCall<'a> {
    ToolCall {
        tool: Tool::WriteFile,
        path,
        grant_id,
    }
}

#[test]
fn portable_path_grammar_denies_ambiguous_forms() {
    for path in [
        "",
        "/etc/passwd",
        "C:/secret",
        "C:secret",
        "a\\b",
        "a//b",
        "a/./b",
        "a/../b",
        "../secret",
        "a/",
        "a\0b",
        "a\nb",
        "src/*",
        "src/?.rs",
        "NUL",
        "COM1.txt",
        "x.",
        "x ",
        "a:b",
    ] {
        assert_eq!(
            validate_relative(path),
            Err(ReasonCode::PathDenied),
            "{path:?}"
        );
    }
    assert!(validate_relative("src/main.rs").is_ok());
    assert!(validate_relative("src/中文.rs").is_ok());
}

#[test]
fn path_membership_is_exact_and_never_url_decoded() {
    for path in [
        "src/main.rs.bak",
        "src/Main.rs",
        "src/%6dain.rs",
        "src/%2e%2e/main.rs",
        "Cargo.toml/other",
    ] {
        assert_eq!(policy().check_path(path), Err(ReasonCode::PathDenied));
    }
}

#[test]
fn policy_rejects_invalid_configuration() {
    for limit in [0, 65_537] {
        assert!(Policy::new(vec!["a".into()], limit).is_err());
    }
    assert!(Policy::new(vec![], 1).is_err());
    assert!(Policy::new(vec!["a".into(), "a".into()], 1).is_err());
    assert!(Policy::new(vec!["../a".into()], 1).is_err());
}

#[test]
fn unknown_tools_never_default_to_read() {
    assert_eq!(Tool::parse("shell"), Err(ReasonCode::UnknownTool));
}

#[test]
fn stage_denial_precedes_path_and_lease_errors() {
    for phase in [Phase::Inspect, Phase::Verify, Phase::Done] {
        assert_eq!(
            policy().evaluate("t1", phase, &[], write("../secret", None), Duration::ZERO),
            Err(ReasonCode::InvalidStage)
        );
    }
}

#[test]
fn reads_are_allowed_only_before_done_without_a_lease() {
    let call = ToolCall {
        tool: Tool::ReadFile,
        path: "src/main.rs",
        grant_id: None,
    };
    for phase in [Phase::Inspect, Phase::Edit, Phase::Verify] {
        assert_eq!(
            policy().evaluate("t1", phase, &[], call, Duration::ZERO),
            Ok(None)
        );
    }
    assert_eq!(
        policy().evaluate("t1", Phase::Done, &[], call, Duration::ZERO),
        Err(ReasonCode::InvalidStage)
    );
}

#[test]
fn writes_require_explicit_current_task_grant() {
    assert_eq!(
        policy().evaluate(
            "t1",
            Phase::Edit,
            &[grant()],
            write("src/main.rs", None),
            now(11).monotonic
        ),
        Err(ReasonCode::LeaseRequired)
    );
    assert_eq!(
        policy().evaluate(
            "t2",
            Phase::Edit,
            &[grant()],
            write("src/main.rs", Some("g1")),
            now(11).monotonic
        ),
        Err(ReasonCode::LeaseNotFound)
    );
    assert_eq!(
        policy().evaluate(
            "t1",
            Phase::Edit,
            &[grant()],
            write("src/main.rs", Some("unknown")),
            now(11).monotonic
        ),
        Err(ReasonCode::LeaseNotFound)
    );
}

#[test]
fn grant_path_binding_is_checked_before_revocation_and_expiry() {
    let mut g = grant();
    g.revoked = true;
    assert_eq!(
        policy().evaluate(
            "t1",
            Phase::Edit,
            &[g],
            write("Cargo.toml", Some("g1")),
            now(100).monotonic
        ),
        Err(ReasonCode::LeaseMismatch)
    );
}

#[test]
fn revoked_precedes_expired_and_expired_precedes_used_up() {
    let mut g = grant();
    g.revoked = true;
    g.consume().unwrap();
    assert_eq!(
        policy().evaluate(
            "t1",
            Phase::Edit,
            &[g.clone()],
            write("src/main.rs", Some("g1")),
            now(100).monotonic
        ),
        Err(ReasonCode::LeaseRevoked)
    );
    g.revoked = false;
    assert_eq!(
        policy().evaluate(
            "t1",
            Phase::Edit,
            &[g],
            write("src/main.rs", Some("g1")),
            now(100).monotonic
        ),
        Err(ReasonCode::LeaseExpired)
    );
}

#[test]
fn ttl_boundary_is_exact_and_independent_of_utc_changes() {
    let g = grant();
    let check = |t| {
        policy().evaluate(
            "t1",
            Phase::Edit,
            std::slice::from_ref(&g),
            write("src/main.rs", Some("g1")),
            t,
        )
    };
    assert_eq!(
        check(Duration::from_secs(70) - Duration::from_nanos(1)),
        Ok(Some(0))
    );
    assert_eq!(
        check(Duration::from_secs(70)),
        Err(ReasonCode::LeaseExpired)
    );
    assert_eq!(
        check(Duration::from_secs(70) + Duration::from_nanos(1)),
        Err(ReasonCode::LeaseExpired)
    );
    let future_wall = TimePoint {
        utc: now(0).utc + chrono::Duration::days(1000),
        ..now(11)
    };
    assert_eq!(check(future_wall.monotonic), Ok(Some(0)));
}

#[test]
fn decision_does_not_consume_and_successful_use_does() {
    let mut g = grant();
    for _ in 0..2 {
        assert_eq!(
            policy().evaluate(
                "t1",
                Phase::Edit,
                std::slice::from_ref(&g),
                write("src/main.rs", Some("g1")),
                now(11).monotonic
            ),
            Ok(Some(0))
        );
    }
    assert_eq!(g.remaining_uses, 1);
    g.consume().unwrap();
    assert_eq!(g.consume(), Err(ReasonCode::LeaseUsedUp));
    assert_eq!(g.remaining_uses, 0);
}

#[test]
fn grant_bounds_reject_zero_and_excessive_values() {
    for (ttl_seconds, max_uses) in [(0, 1), (301, 1), (1, 0), (1, 11)] {
        assert!(
            Grant::issue(
                GrantSpec {
                    grant_id: "g".into(),
                    task_id: "t".into(),
                    path: "src/main.rs".into(),
                    ttl_seconds,
                    max_uses
                },
                now(0)
            )
            .is_err()
        );
    }
}

#[test]
fn snapshot_status_priority_matches_contract() {
    let mut g = grant();
    g.consume().unwrap();
    g.revoked = true;
    assert_eq!(
        g.status(Phase::Verify, now(100).monotonic),
        GrantStatus::StageInactive
    );
    assert_eq!(
        g.status(Phase::Edit, now(100).monotonic),
        GrantStatus::Revoked
    );
    g.revoked = false;
    assert_eq!(
        g.status(Phase::Edit, now(100).monotonic),
        GrantStatus::Expired
    );
    assert_eq!(
        g.status(Phase::Edit, now(11).monotonic),
        GrantStatus::UsedUp
    );
}

#[test]
fn physical_check_distinguishes_regular_missing_and_directory() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().canonicalize().unwrap();
    fs::create_dir(root.join("src")).unwrap();
    fs::write(root.join("src/main.rs"), "safe").unwrap();
    assert_eq!(
        resolve_existing(&root, "src/main.rs").unwrap(),
        root.join("src/main.rs")
    );
    assert_eq!(
        resolve_existing(&root, "absent.rs"),
        Err(PathError::Missing)
    );
    assert_eq!(resolve_existing(&root, "src"), Err(PathError::Denied));
}

#[cfg(unix)]
#[test]
fn file_and_parent_symlinks_are_denied_even_when_internal() {
    use std::os::unix::fs::symlink;
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().canonicalize().unwrap();
    fs::create_dir(root.join("real")).unwrap();
    fs::write(root.join("real/file"), "unchanged").unwrap();
    symlink(root.join("real/file"), root.join("linked")).unwrap();
    symlink(root.join("real"), root.join("parent")).unwrap();
    assert_eq!(resolve_existing(&root, "linked"), Err(PathError::Denied));
    assert_eq!(
        resolve_existing(&root, "parent/file"),
        Err(PathError::Denied)
    );
    assert_eq!(
        fs::read_to_string(root.join("real/file")).unwrap(),
        "unchanged"
    );
}
