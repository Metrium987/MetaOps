import json
import logging
import os
import shutil
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from mcp import StdioServerParameters
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)

logger = logging.getLogger(__name__)


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _url_reachable(url: str, timeout: float = 1.0) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "mcp_servers.json"


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

    try:
        if not path.exists():
            # Auto-copy from example if available
            example = path.parent / (path.name + ".example")
            if example.exists():
                import shutil as _shutil
                _shutil.copy(example, path)
                logger.info("mcp_servers.json created from mcp_servers.json.example — edit it to add your real paths/keys")
            else:
                fallback_url = os.getenv("MCP_SERVER_URL", "").strip()
                if fallback_url:
                    logger.info("mcp_servers.json not found — falling back to MCP_SERVER_URL=%s", fallback_url)
                    return [McpToolset(connection_params=SseConnectionParams(url=fallback_url))]
                return []

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.error("Failed to load mcp_servers.json: %s", exc)
        return []

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


def _env_looks_valid(env: dict) -> tuple[bool, str]:
    """Return (ok, reason) — skip the server if any env var is empty or a placeholder."""
    for key, value in env.items():
        if not value or not str(value).strip():
            return False, f"env var {key} is empty"
        val = str(value).strip()
        # Common placeholder patterns: "...", "xxx", "<token>", "YOUR_*", "sk-...", "ghp_..."
        if (val.endswith("...") or val.startswith("<") or val.upper().startswith("YOUR_")
                or "your_" in val.lower() or "placeholder" in val.lower()
                or "token_here" in val.lower() or "api_key_here" in val.lower()):
            return False, f"env var {key} looks like a placeholder ({val!r})"
    return True, ""


def _expand_arg(arg: str) -> str:
    """Expand environment variables and ~ in path-like arguments."""
    return os.path.expanduser(os.path.expandvars(arg))


def _arg_looks_like_wrong_platform_path(arg: str) -> bool:
    """Return True if the arg is a Windows absolute path but we're on Linux/macOS."""
    if sys.platform == "win32":
        return False
    # Detect C:\... or C:/... style Windows paths
    return len(arg) >= 3 and arg[1] == ":" and arg[2] in ("/", "\\")


def _build_toolset(name: str, cfg: dict) -> McpToolset | None:
    if "command" in cfg:
        command = cfg["command"]
        if not _command_available(command):
            logger.warning(
                "MCP server '%s' skipped — command '%s' not found in PATH "
                "(install it or remove this server from mcp_servers.json)",
                name, command,
            )
            return None
        env = cfg.get("env") or {}
        # Skip servers whose required env vars are missing or placeholder
        if env:
            ok, reason = _env_looks_valid(env)
            if not ok:
                logger.warning(
                    "MCP server '%s' skipped — %s "
                    "(set the real value in mcp_servers.json or .env)",
                    name, reason,
                )
                return None
        # Expand env vars and ~ in args; skip if any arg is a wrong-OS path
        raw_args = cfg.get("args", [])
        expanded_args = []
        skip = False
        for arg in raw_args:
            expanded = _expand_arg(str(arg))
            if _arg_looks_like_wrong_platform_path(expanded):
                logger.warning(
                    "MCP server '%s' skipped — arg %r looks like a Windows path "
                    "(update mcp_servers.json with the correct path for this OS)",
                    name, arg,
                )
                skip = True
                break
            expanded_args.append(expanded)
        if skip:
            return None

        merged_env = {**os.environ, **env} if env else None
        params = StdioConnectionParams(
            server_params=StdioServerParameters(
                command=command,
                args=expanded_args,
                env=merged_env,
            ),
            timeout=cfg.get("timeout", 30.0),
        )
        logger.debug("MCP stdio '%s': %s %s", name, command, expanded_args)
        return McpToolset(connection_params=params)

    if "url" in cfg:
        url = cfg["url"]
        parsed = urlparse(url)
        # Skip localhost/127.0.0.1 servers that aren't actually running
        if parsed.hostname in ("localhost", "127.0.0.1", "::1") and not _url_reachable(url):
            logger.warning(
                "MCP server '%s' skipped — %s is not reachable "
                "(start the server or remove it from mcp_servers.json)",
                name, url,
            )
            return None
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
