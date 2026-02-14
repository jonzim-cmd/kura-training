from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


MCP_LIB = REPO_ROOT / "mcp" / "src" / "lib.rs"
CLI_MCP_COMMAND = REPO_ROOT / "cli" / "src" / "commands" / "mcp.rs"
MCP_RUNTIME_LIB = REPO_ROOT / "mcp-runtime" / "src" / "lib.rs"
WORKSPACE_CARGO = REPO_ROOT / "Cargo.toml"
CLI_CARGO = REPO_ROOT / "cli" / "Cargo.toml"
MCP_CARGO = REPO_ROOT / "mcp" / "Cargo.toml"


def test_mcp_runtime_is_not_path_included_from_cli() -> None:
    src = MCP_LIB.read_text(encoding="utf-8")
    assert "#[path" not in src
    assert "kura_mcp_runtime" in src


def test_cli_and_mcp_depend_on_shared_runtime_crate() -> None:
    cli_cargo = CLI_CARGO.read_text(encoding="utf-8")
    mcp_cargo = MCP_CARGO.read_text(encoding="utf-8")
    workspace_cargo = WORKSPACE_CARGO.read_text(encoding="utf-8")

    assert "kura-mcp-runtime = { path = \"mcp-runtime\" }" in workspace_cargo
    assert "kura-mcp-runtime = { workspace = true }" in cli_cargo
    assert "kura-mcp-runtime = { workspace = true }" in mcp_cargo


def test_shared_runtime_exports_command_surface() -> None:
    src = MCP_RUNTIME_LIB.read_text(encoding="utf-8")
    assert "pub enum McpCommands" in src
    assert "pub struct McpServeArgs" in src
    assert "pub async fn run" in src
