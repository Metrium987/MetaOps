import json
import logging
import os
from pathlib import Path

from mcp import StdioServerParameters
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parents[3] / "mcp_servers.json"


def load_mcp_toolsets(config_path: Path | str | None = None) -> list[McpToolset]:
    """Load McpToolset instances from mcp_servers.json.

    File format (same as claude_desktop_config.json):
    {
      "mcpServers": {
        "server-name": {
          "command": "npx",               <- stdio process
          "args": ["-y", "@org/server"],
          "env": {"MY_KEY": "value"}
        },
        "sse-server": {
          "url": "http://localhost:8000/sse",  <- SSE (default when url is present)
          "headers": {"Authorization": "Bearer ..."}
        },
        "http-server": {
          "url": "http://localhost:9000/mcp",
          "transport": "http"                  <- Streamable HTTP
        }
      }
    }

    Falls back to MCP_SERVER_URL env var if mcp_servers.json does not exist.
    """
    path = Path(config_path) if config_path else _CONFIG_PATH

    if not path.exists():
        fallback_url = os.getenv("MCP_SERVER_URL", "").strip()
        if fallback_url:
            logger.info("mcp_servers.json not found — falling back to MCP_SERVER_URL=%s", fallback_url)
            return [McpToolset(connection_params=SseConnectionParams(url=fallback_url))]
        return []

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    toolsets: list[McpToolset] = []
    for name, cfg in data.get("mcpServers", {}).items():
        try:
            toolset = _build_toolset(name, cfg)
            if toolset:
                toolsets.append(toolset)
        except Exception as exc:
            logger.warning("MCP server '%s' skipped: %s", name, exc)

    logger.info("%d MCP server(s) loaded", len(toolsets))
    return toolsets


def _build_toolset(name: str, cfg: dict) -> McpToolset | None:
    if "command" in cfg:
        env = cfg.get("env") or {}
        merged_env = {**os.environ, **env} if env else None
        params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=merged_env,
            ),
            timeout=cfg.get("timeout", 10.0),
        )
        logger.debug("MCP stdio '%s': %s %s", name, cfg["command"], cfg.get("args", []))
        return McpToolset(connection_params=params)

    if "url" in cfg:
        url = cfg["url"]
        headers = cfg.get("headers") or None
        transport = cfg.get("transport", "sse").lower()
        if transport == "http":
            params = StreamableHTTPConnectionParams(url=url, headers=headers)
        else:
            params = SseConnectionParams(url=url, headers=headers)
        logger.debug("MCP %s '%s': %s", transport, name, url)
        return McpToolset(connection_params=params)

    logger.warning("MCP server '%s': missing 'command' or 'url' — skipped", name)
    return None
