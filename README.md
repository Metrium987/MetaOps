# MetaOps

Enterprise-grade autonomous AI agent built on [Google ADK 2.3.0](https://google.github.io/adk-docs/). Runs on Telegram and CLI. Persistent memory, multi-provider LLM support, automated coding workflows, and deep web research — all configurable from a single `.env`.

---

## Features

- **Multi-provider LLM** — 30 providers supported (OpenRouter, OpenAI, Anthropic, Gemini, DeepSeek, Groq, Mistral, xAI, Ollama, LM Studio, and more). Each agent profile can use a different provider and model.
- **Persistent memory** — SQLite for sessions, ChromaDB for vector memory (episodic, semantic, procedural, persona). Past conversations are automatically recalled via `preload_memory`.
- **Skill system** — The agent learns reusable procedures during conversations and executes them on demand.
- **Vibe coding workflow** — Coder agent → reviewer agent → correction loop (up to 3 revisions). Automatic code review on every generation.
- **Full dev cycle** — Architect produces an implementation plan, coder implements it with review loop, optional test runner.
- **Deep web research** — Parallel web search + extraction + crawling via Tavily, synthesized into a structured report.
- **Multi-server MCP** — Configure multiple MCP servers (SSE, stdio, streamable HTTP) via `mcp_servers.json`.
- **Background audit** — Scheduled deep scan: bandit (security), pip-audit (CVEs), code quality patterns, dependency review.
- **Telegram gateway** — `/start`, `/clear`, per-user sessions, message chunking.
- **CLI gateway** — Interactive prompt with history.
- **Cron scheduler** — APScheduler for unattended background tasks.
- **Strict contract** — Every response starts with `[STATUS: OK|BLOCKED|PENDING]`. Think before act. Source every claim. Surgical scope.

---

## Architecture

```
MetaOps/
├── bootstrap.py                 # Remote one-liner: clone + uv venv + install + data dirs (no repo needed yet)
├── install.py                   # Local setup once cloned: pip install -e ., optional tools, .env, smoke test
├── pyproject.toml               # Package metadata, dependencies, `metaops` CLI entry point
├── mcp_servers.json(.example)   # MCP server definitions consumed by tools/mcp_loader.py
├── .env(.example)               # Provider keys, per-agent model routing, roles, paths
│
└── src/metaops/
    ├── main.py                  # argparse CLI entry point — wires services, picks CLI vs Telegram gateway
    ├── config.py                 # MetaOpsConfig + ModelConfig — provider registry, .env parsing, native driver routing
    │
    ├── backends/
    │   └── local.py               # LocalTerminalBackend — streams shell output, bounded timeout/output size
    │
    ├── core/
    │   ├── root.py                 # Builds the coordinator Agent + Runner (system prompt, tools, services)
    │   ├── callbacks.py             # Memory auto-injection, skill harvesting, tool/model error callbacks
    │   ├── background.py            # Audit workflow tools (bandit, pip-audit, source pattern scan)
    │   └── local_llm_driver.py      # OpenAILlm hardened for local backends (Ollama/LM Studio) — recovers missing tool_call id/name
    │
    ├── gateway/
    │   ├── base.py                  # PlatformBridge / BaseGateway abstract interfaces
    │   ├── cli.py                   # CLIBridge — interactive prompt_toolkit console
    │   ├── telegram.py               # TelegramBridge — bot polling, per-user sessions, RBAC, allowlist
    │   ├── delivery.py               # DeliveryService — routes cron/system messages to "cli" or "telegram:<chat_id>"
    │   ├── registry.py               # GatewayRegistry — tracks which gateway(s) are currently active
    │   └── session_manager.py        # Maps user_id → session_id, tracks which sessions are busy
    │
    ├── memory/
    │   ├── database.py               # MemoryDatabase — SQLite store for learned procedural skills
    │   ├── embeddings.py             # MetaOpsEmbeddingFunction — local ONNX or API embeddings for ChromaDB
    │   └── vector_service.py         # HybridVectorMemoryService — episodic/semantic/procedural/persona ChromaDB cubes
    │
    ├── scheduler/
    │   └── cron.py                   # MetaOpsCronScheduler — APScheduler-driven unattended jobs
    │
    ├── tools/
    │   ├── _shell_guard.py           # check_command_allowed() — denylist gate (rm/sudo/mkfs/...) for non-admin roles
    │   ├── mcp_loader.py              # Loads MCP servers (stdio/SSE/streamable HTTP) from mcp_servers.json
    │   ├── memory_tools.py            # save_procedural_skill / recall_past_context agent tools
    │   ├── rag_tools.py               # ingest_file_dependency — indexes a local file into semantic memory
    │   ├── secure_toolset.py          # execute_secure_command — role-gated shell tool (admin/user/guest)
    │   ├── skill_executor.py          # execute_skill — replays a learned skill, shell-quotes caller args
    │   ├── web_search.py              # Tavily-backed search / extract / crawl / map / company_info tools
    │   └── workstream.py              # execute_workstream_command + workstream_executor sub-agent (long pipelines)
    │
    └── workflows/
        ├── agent_runner.py            # run_agent_once() — shared throwaway Runner/session helper for the workflows below
        ├── vibe_coding.py              # Coder → Reviewer revision loop (up to MAX_REVISIONS=3 passes)
        ├── dev_cycle.py                 # Architect plan → vibe_code → optional test run
        ├── research.py                  # Researcher (Tavily) → Synthesizer — structured report
        └── thinker.py                    # Deep-reasoning sub-agent exposed as AgentTool
```

---

## Module Lexicon

| File | Role |
|------|------|
| `main.py` | CLI entry point (`metaops` command). Parses args, builds the `Runner`, registers gateways, starts the cron scheduler, picks CLI or Telegram. |
| `config.py` | `MetaOpsConfig` (global settings) + `ModelConfig` (per-agent provider/model/key/`max_tokens`, picks the fastest native driver: Anthropic → Gemini → OpenAI-compatible → LiteLLM fallback). |
| `backends/local.py` | `LocalTerminalBackend` — runs a shell command and streams output, bounded by a wall-clock timeout and a max-output-bytes cap. |
| `core/root.py` | Assembles the coordinator `Agent`: system prompt (the "strict contract"), every tool/workflow, session/memory/artifact services, then builds the `Runner`. |
| `core/callbacks.py` | `before_agent`/`after_agent` hooks — auto-injects relevant memory before a turn, harvests reusable skills after one; also tool/model error callbacks and a sensitive-tool list requiring extra scrutiny. |
| `core/background.py` | Tools used by the nightly audit job: list/read source files, run `bandit` + `pip-audit`, scan for code-quality patterns. |
| `core/local_llm_driver.py` | `LocalOpenAILlm(OpenAILlm)` — patches responses from local/self-hosted OpenAI-compatible servers that omit `tool_call.id`/`name`, which would otherwise silently break the tool-call turn. |
| `gateway/base.py` | `PlatformBridge`/`BaseGateway` — abstract `start()`/`stop()`/`send_event()` contract all gateways implement. |
| `gateway/cli.py` | `CLIBridge` — interactive console loop (`prompt_toolkit`), feeds user input into the `Runner`. |
| `gateway/telegram.py` | `TelegramBridge` — Telegram bot polling, `/start`/`/clear`, per-user sessions, optional user allowlist, role assignment. |
| `gateway/delivery.py` | `DeliveryService` — sends a message to a delivery target string (`"cli"` or `"telegram:<chat_id>"`), used by the cron scheduler to report results. |
| `gateway/registry.py` | `GatewayRegistry` — tiny registry of which gateway instances exist and whether each is currently active. |
| `gateway/session_manager.py` | Maps `user_id` → stable `session_id` per platform, and tracks which sessions are mid-turn ("busy") to avoid concurrent runs. |
| `memory/database.py` | `MemoryDatabase` — SQLite table of learned skills (`name`, `trigger_pattern`, `procedure`). |
| `memory/embeddings.py` | `MetaOpsEmbeddingFunction` — ChromaDB embedding function, either local ONNX (no key) or an OpenAI-compatible API. |
| `memory/vector_service.py` | `HybridVectorMemoryService` — four ChromaDB collections (episodic, semantic, procedural, persona) implementing ADK's `BaseMemoryService`. |
| `scheduler/cron.py` | `MetaOpsCronScheduler` — wraps APScheduler; runs a prompt unattended on a cron expression and forwards the final text via a delivery callback. |
| `tools/_shell_guard.py` | `check_command_allowed()` — shared denylist (`rm`, `sudo`, `mkfs`, `format`, `dd`) gating shell tools for non-admin roles. Defense-in-depth only, not a sandbox. |
| `tools/mcp_loader.py` | Reads `mcp_servers.json`, checks each server is reachable/the command exists, and builds `McpToolset`s (stdio/SSE/streamable HTTP). |
| `tools/memory_tools.py` | `save_procedural_skill` (persists a skill as a versioned ADK artifact) and `recall_past_context` (semantic search over past sessions). |
| `tools/rag_tools.py` | `ingest_file_dependency` — reads/chunks/indexes a local file into the semantic memory cube, with path-traversal protection and a guest-role block. |
| `tools/secure_toolset.py` | `SecureMetaOpsToolset` — exposes `execute_secure_command`, gated by `_shell_guard` and the caller's `user:role`. |
| `tools/skill_executor.py` | `execute_skill` — looks up a learned procedure and runs it with caller-supplied arguments, shell-quoted to prevent injection. |
| `tools/web_search.py` | Tavily client wrappers: `web_search`, `web_extract`, `web_crawl`, `web_map`, `company_info`. |
| `tools/workstream.py` | `execute_workstream_command` (long-running pipeline tool) plus a dedicated `workstream_executor` sub-agent for multi-step bash jobs. |
| `workflows/agent_runner.py` | `run_agent_once()` — creates a throwaway `Runner` + in-memory session, sends one prompt, returns the concatenated text output (filters out `thought` parts). Shared by the three workflows below. |
| `workflows/vibe_coding.py` | Coder agent writes code → Reviewer agent checks it → up to `MAX_REVISIONS` (3) automatic correction passes. |
| `workflows/dev_cycle.py` | Architect agent produces an implementation plan → `vibe_code` implements it → optional test run. |
| `workflows/research.py` | Researcher agent gathers material via the Tavily tools → Synthesizer agent produces a structured report. |
| `workflows/thinker.py` | Single deep-reasoning agent (problem breakdown → step-by-step analysis → tradeoffs → recommendation), exposed as an `AgentTool`. |

---

## Stack

| Component | Library |
|-----------|---------|
| Agent framework | `google-adk 2.3.0` |
| LLM routing | `litellm` (via ADK LiteLlm) |
| Vector memory | `chromadb` |
| Session storage | `aiosqlite` |
| Web search | `tavily-python` |
| Telegram | `python-telegram-bot` |
| Scheduler | `apscheduler` |
| CLI | `prompt_toolkit` |

---

## Installation

**One command — clones, installs, and configures everything:**

```bash
curl -sSL https://raw.githubusercontent.com/Metrium987/MetaOps/main/bootstrap.py | python -
```

On PowerShell (Windows):

```powershell
irm https://raw.githubusercontent.com/Metrium987/MetaOps/main/bootstrap.py | python -
```

**Requirements:** Python 3.10+, Git

The script:
- Clones the repo into `./MetaOps/`
- `pip install -e .` (all dependencies)
- Installs `bandit` and `pip-audit` (optional — used by the audit workflow)
- Creates `./data/` directories
- Copies `.env.example` → `.env` if no `.env` exists

---

### Manual install

```bash
git clone https://github.com/Metrium987/MetaOps.git
cd MetaOps
pip install -e .
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Minimum required — pick one provider:

```env
# Recommended starting point (300+ models via one key)
OPENROUTER_API_KEY=sk-or-...

# Web search
TAVILY_API_KEY=tvly-...

# Telegram (optional)
TELEGRAM_BOT_TOKEN=...
```

Each agent can use a different provider:

```env
METAOPS_COORDINATOR_PROVIDER=anthropic
METAOPS_COORDINATOR_MODEL=claude-opus-4-8-20251001

METAOPS_WORKSTREAM_PROVIDER=groq
METAOPS_WORKSTREAM_MODEL=llama-3.3-70b-versatile
```

If `METAOPS_*_MODEL` is left empty, a sensible default is picked automatically for the selected provider.

### MCP Servers

Edit `mcp_servers.json` to add MCP servers:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
    },
    "custom-sse": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

---

## Usage

MetaOps provides separate commands to launch the CLI console (TUI) or the Telegram gateway independently.

### CLI Gateway (Console TUI)

Starts the interactive prompt with history.

```bash
# Standard command (defaults to CLI)
metaops

# Or explicitly specifying the CLI gateway
metaops gateway cli
```

### Telegram Gateway

Starts the Telegram bot polling listener.

```bash
metaops gateway telegram
```

### Global CLI Options

* `--no-cron` — Disable the background cron job scheduler.
* `--debug` — Enable verbose debugging logs (silenced by default for a clean prompt).

---

## Security & Role Management

MetaOps implements role-based access control (RBAC) to restrict system and file operations based on the user's role:
* **`admin`** — Full shell execution and complete access to all workflows and tools.
* **`user`** — Restricted shell access (destructive keywords like `sudo`, `rm`, `mkfs`, etc. are blocked).
* **`guest`** — No shell or system access. Sensitive tools (`full_dev_cycle`, `execute_skill`, `ingest_file_dependency`) are entirely blocked.

You can configure the default roles and delivery targets inside your `.env` file:

```env
# Default role for CLI session (default: admin)
METAOPS_DEFAULT_CLI_ROLE=admin

# Default role for background cron scheduler (default: admin)
METAOPS_DEFAULT_CRON_ROLE=admin

# Default role for Telegram bot sessions (default: admin)
METAOPS_DEFAULT_TELEGRAM_ROLE=admin

# Target for cron delivery notifications ("cli" or "telegram:<chat_id>")
METAOPS_CRON_DELIVERY_TARGET=cli
```


---

## Workflows

| Tool | What it does |
|------|-------------|
| `vibe_code` | Write code → auto review → fix loop (max 3 revisions) |
| `full_dev_cycle` | Architect plan → vibe_code → optional test run |
| `deep_research` | Parallel Tavily search + extraction → structured report |
| `thinker` | Deep reasoning for hard decisions and tradeoffs |
| `run_audit` | bandit + pip-audit + code pattern scan + dependency review |
| `workstream_executor` | Isolated bash pipeline (long-running commands) |
| `execute_secure_command` | Role-gated shell execution |
| `execute_skill` | Run a previously learned procedure from the skills DB |
| `ingest_file_dependency` | Index a local file into semantic memory |

---

## Supported Providers

OpenRouter · NousResearch · Novita · Kilo Code · OpenCode · OpenAI · Anthropic · Gemini · xAI · DeepSeek · Mistral · Groq · Perplexity · Cohere · Together AI · Fireworks · NVIDIA · Hugging Face · GitHub Copilot · Arcee · GMI · Azure AI Foundry · Alibaba (Qwen) · Kimi · MiniMax · StepFun · Z.AI (GLM) · Xiaomi MiMo · Tencent TokenHub · Ollama · LM Studio

---

## License

MIT — see [LICENSE](LICENSE)
