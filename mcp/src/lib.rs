mod util;

#[path = "../../cli/src/commands/mcp.rs"]
mod runtime;

pub use runtime::{McpCommands, run};
