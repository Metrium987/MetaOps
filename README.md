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
metaops/
├── core/
│   ├── root.py          # Agent + Runner factory (coordinator, tools, callbacks)
│   ├── callbacks.py     # Skill loading (before) + skill harvest (after)
│   └── background.py    # Deep audit workflow (bandit, pip-audit, code scan)
├── workflows/
│   ├── vibe_coding.py   # Coder → Reviewer → loop
│   ├── dev_cycle.py     # Planner → vibe_code → optional tests
│   ├── research.py      # Researcher (Tavily) → Synthesizer
│   └── thinker.py       # Deep reasoning sub-agent
├── tools/
│   ├── web_search.py    # Tavily: search, extract, crawl, map, company_info
│   ├── mcp_loader.py    # Multi-server MCP loader (mcp_servers.json)
│   ├── secure_toolset.py # Role-gated shell execution
│   ├── workstream.py    # Isolated bash pipeline executor
│   ├── skill_executor.py # Execute learned skills from SQLite
│   └── rag_tools.py     # File ingestion into semantic memory
├── memory/
│   ├── session_service.py # SQLite session persistence
│   ├── vector_service.py  # ChromaDB (episodic/semantic/procedural/persona)
│   └── database.py        # Skills SQLite DB
├── gateway/
│   ├── telegram.py      # Telegram bot gateway
│   └── cli.py           # Interactive CLI gateway
└── scheduler/
    └── cron.py          # APScheduler cron runner
```

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
