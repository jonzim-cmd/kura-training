use clap::{Args, Subcommand};
use tokio::process::Command;
use uuid::Uuid;

use crate::util::exit_error;

#[derive(Subcommand)]
pub enum EvalCommands {
    /// Run baseline-vs-candidate shadow evaluation
    Shadow(EvalShadowArgs),
}

#[derive(Args, Clone)]
pub struct EvalCommonArgs {
    /// User UUID whose inference projections should be replayed
    #[arg(long)]
    pub user_id: Uuid,

    /// Optional projection type filter (repeatable)
    #[arg(
        long = "projection-type",
        value_parser = ["semantic_memory", "strength_inference", "readiness_inference", "causal_inference"]
    )]
    pub projection_type: Vec<String>,

    /// Engine override used during strength replay windows
    #[arg(long, default_value = "closed_form", value_parser = ["closed_form", "pymc"])]
    pub strength_engine: String,

    /// Candidate cutoff used for semantic ranking metrics
    #[arg(long, default_value_t = 5)]
    pub semantic_top_k: u32,

    /// Replay source mode
    #[arg(long, default_value = "both", value_parser = ["projection_history", "event_store", "both"])]
    pub source: String,

    /// Do not persist run + artifacts in inference_eval tables
    #[arg(long)]
    pub no_persist: bool,
}

#[derive(Args, Clone)]
pub struct EvalShadowArgs {
    #[command(flatten)]
    pub common: EvalCommonArgs,

    /// Candidate strength engine (defaults to baseline strength engine)
    #[arg(long, value_parser = ["closed_form", "pymc"])]
    pub candidate_strength_engine: Option<String>,

    /// Candidate replay source (defaults to baseline source)
    #[arg(long, value_parser = ["projection_history", "event_store", "both"])]
    pub candidate_source: Option<String>,

    /// Candidate semantic top-k (defaults to baseline semantic-top-k)
    #[arg(long)]
    pub candidate_semantic_top_k: Option<u32>,
}

pub async fn run(command: EvalCommands) -> i32 {
    match command {
        EvalCommands::Shadow(args) => run_shadow(args).await,
    }
}

async fn run_shadow(args: EvalShadowArgs) -> i32 {
    let mut worker_args = build_common_worker_args(&args.common);
    worker_args.push("--shadow".to_string());

    if let Some(candidate_strength_engine) = args.candidate_strength_engine {
        worker_args.push("--candidate-strength-engine".to_string());
        worker_args.push(candidate_strength_engine);
    }
    if let Some(candidate_source) = args.candidate_source {
        worker_args.push("--candidate-source".to_string());
        worker_args.push(candidate_source);
    }
    if let Some(candidate_semantic_top_k) = args.candidate_semantic_top_k {
        worker_args.push("--candidate-semantic-top-k".to_string());
        worker_args.push(candidate_semantic_top_k.to_string());
    }

    execute_worker_eval_cli(&worker_args).await
}

fn build_common_worker_args(common: &EvalCommonArgs) -> Vec<String> {
    let mut worker_args = vec![
        "--user-id".to_string(),
        common.user_id.to_string(),
        "--strength-engine".to_string(),
        common.strength_engine.clone(),
        "--semantic-top-k".to_string(),
        common.semantic_top_k.to_string(),
        "--source".to_string(),
        common.source.clone(),
    ];

    for projection_type in &common.projection_type {
        worker_args.push("--projection-type".to_string());
        worker_args.push(projection_type.clone());
    }

    if common.no_persist {
        worker_args.push("--no-persist".to_string());
    }

    worker_args
}

async fn execute_worker_eval_cli(worker_args: &[String]) -> i32 {
    let status = match Command::new("uv")
        .args([
            "run",
            "--project",
            "workers",
            "python",
            "-m",
            "kura_workers.eval_cli",
        ])
        .args(worker_args)
        .status()
        .await
    {
        Ok(status) => status,
        Err(err) => {
            exit_error(
                &format!("Failed to launch eval runner via uv: {err}"),
                Some(
                    "Ensure `uv` is installed and workers environment is available. Fallback: `uv run --project workers python -m kura_workers.eval_cli --shadow ...`",
                ),
            );
        }
    };

    status.code().unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use super::{EvalCommonArgs, build_common_worker_args};
    use uuid::Uuid;

    #[test]
    fn build_common_worker_args_serializes_required_fields() {
        let args = EvalCommonArgs {
            user_id: Uuid::parse_str("11111111-1111-1111-1111-111111111111").unwrap(),
            projection_type: vec![
                "semantic_memory".to_string(),
                "strength_inference".to_string(),
            ],
            strength_engine: "pymc".to_string(),
            semantic_top_k: 7,
            source: "event_store".to_string(),
            no_persist: true,
        };

        let serialized = build_common_worker_args(&args);
        assert!(serialized.contains(&"--user-id".to_string()));
        assert!(serialized.contains(&"11111111-1111-1111-1111-111111111111".to_string()));
        assert!(serialized.contains(&"--projection-type".to_string()));
        assert!(serialized.contains(&"semantic_memory".to_string()));
        assert!(serialized.contains(&"strength_inference".to_string()));
        assert!(serialized.contains(&"--strength-engine".to_string()));
        assert!(serialized.contains(&"pymc".to_string()));
        assert!(serialized.contains(&"--semantic-top-k".to_string()));
        assert!(serialized.contains(&"7".to_string()));
        assert!(serialized.contains(&"--source".to_string()));
        assert!(serialized.contains(&"event_store".to_string()));
        assert!(serialized.contains(&"--no-persist".to_string()));
    }
}
