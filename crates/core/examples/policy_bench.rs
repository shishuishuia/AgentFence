//! Reproducible pure-policy samples; stdout is machine-readable JSON.

use agentfence_core::{Grant, GrantSpec, Phase, Policy, TimePoint, Tool, ToolCall};
use chrono::Utc;
use serde::Serialize;
use std::{
    hint::black_box,
    time::{Duration, Instant},
};

#[derive(Serialize)]
struct Samples {
    scenario: &'static str,
    round: usize,
    p50_us: f64,
    p95_us: f64,
    p99_us: f64,
    samples_us: Vec<f64>,
}

fn percentile(sorted: &[f64], percentile: usize) -> f64 {
    sorted[(sorted.len() * percentile).div_ceil(100).saturating_sub(1)]
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let iterations: usize = std::env::args().nth(1).map_or(Ok(1000), |s| s.parse())?;
    if iterations < 1000 {
        return Err("iterations must be at least 1000 per scenario per round".into());
    }
    let policy = Policy::new(vec!["src/main.rs".into()], 65536)?;
    let time = TimePoint {
        monotonic: Duration::ZERO,
        utc: Utc::now(),
    };
    let grants = vec![Grant::issue(
        GrantSpec {
            grant_id: "bench-g".into(),
            task_id: "bench-t".into(),
            path: "src/main.rs".into(),
            ttl_seconds: 60,
            max_uses: 1,
        },
        time,
    )?];
    let call = ToolCall {
        tool: Tool::WriteFile,
        path: "src/main.rs",
        grant_id: Some("bench-g"),
    };
    let mut results = Vec::new();
    for round in 1..=3 {
        for (scenario, phase) in [
            ("allow_write_valid_lease", Phase::Edit),
            ("deny_write_verify", Phase::Verify),
        ] {
            for _ in 0..256 {
                let _ = black_box(policy.evaluate("bench-t", phase, &grants, call, Duration::ZERO));
            }
            let mut samples_us = Vec::with_capacity(iterations);
            for _ in 0..iterations {
                let start = Instant::now();
                let result = black_box(policy.evaluate(
                    black_box("bench-t"),
                    black_box(phase),
                    black_box(&grants),
                    black_box(call),
                    black_box(Duration::ZERO),
                ));
                let elapsed = start.elapsed().as_secs_f64() * 1_000_000.0;
                if result.is_ok() != (phase == Phase::Edit) {
                    return Err("unexpected policy decision".into());
                }
                samples_us.push(elapsed);
            }
            let mut sorted = samples_us.clone();
            sorted.sort_by(f64::total_cmp);
            results.push(Samples {
                scenario,
                round,
                p50_us: percentile(&sorted, 50),
                p95_us: percentile(&sorted, 95),
                p99_us: percentile(&sorted, 99),
                samples_us,
            });
        }
    }
    println!(
        "{}",
        serde_json::to_string(
            &serde_json::json!({"measurement":"pure_policy", "iterations_per_round":iterations,
        "rounds":3,"warmup_per_scenario":256,"percentile_method":"nearest_rank","clock":"std::time::Instant",
        "excludes":["http","filesystem_metadata","file_content_io","audit","task_lock"],"results":results})
        )?
    );
    Ok(())
}
