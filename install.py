"""
MetaOps — local installer (run inside the cloned repo)
Usage: cd MetaOps && python install.py
"""
import sys
import os
import shutil
import subprocess
from pathlib import Path

# Force UTF-8 on Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Resolve project root ONCE ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR     = PROJECT_ROOT / ".data"
ENV_FILE     = PROJECT_ROOT / ".env"
ENV_EXAMPLE  = PROJECT_ROOT / ".env.example"

# ── Colors ────────────────────────────────────────────────────────────────────
OK    = "\033[92m[OK]\033[0m"
FAIL  = "\033[91m[FAIL]\033[0m"
WARN  = "\033[93m[WARN]\033[0m"
INFO  = "\033[94m[-->]\033[0m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def ok(msg):   print(f"  {OK}  {msg}")
def fail(msg): print(f"  {FAIL}  {msg}"); sys.exit(1)
def warn(msg): print(f"  {WARN}  {msg}")
def info(msg): print(f"  {INFO}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")


# ── 1. Python version ─────────────────────────────────────────────────────────
header("1 / 7  Python version")
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    fail(f"Python 3.10+ required — found {major}.{minor}")
ok(f"Python {major}.{minor}")


# ── 2. pip ────────────────────────────────────────────────────────────────────
header("2 / 7  pip")
pip = shutil.which("pip") or shutil.which("pip3")
if not pip:
    fail("pip not found — install pip first")
ok(pip)


# ── 3. Install MetaOps package ────────────────────────────────────────────────
header("3 / 7  Installing MetaOps + dependencies")
info("Running: pip install -e .")
result = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
    capture_output=True, text=True, cwd=str(PROJECT_ROOT)
)
if result.returncode != 0:
    print(result.stderr[-2000:])
    fail("pip install failed — see errors above")
ok("MetaOps installed")


# ── 4. Optional audit tools ───────────────────────────────────────────────────
header("4 / 7  Optional tools (audit workflow)")
for tool, package in [("bandit", "bandit"), ("pip-audit", "pip-audit")]:
    if shutil.which(tool):
        ok(f"{tool} already installed")
    else:
        info(f"Installing {package}...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--quiet"],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            ok(f"{tool} installed")
        else:
            warn(f"{tool} could not be installed — audit scans will skip it")


# ── 5. Node MCP servers ────────────────────────────────────────────────────────
header("5 / 7  Node MCP servers")
npm = shutil.which("npm")
if npm:
    info("Installing local MCP servers via npm (filesystem, memory)...")
    r = subprocess.run(
        [npm, "install", "@modelcontextprotocol/server-filesystem", "@modelcontextprotocol/server-memory", "@portkey-ai/gateway", "--quiet"],
        cwd=str(PROJECT_ROOT)
    )
    if r.returncode == 0:
        ok("MCP servers and Portkey gateway installed locally")
    else:
        warn("Failed to install local MCP/Portkey servers via npm — npx fallback will be used at runtime")
else:
    warn("npm not found — skipping local MCP server installation")


# ── 6. Data directories ───────────────────────────────────────────────────────
header("6 / 7  Data directories")
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "artifacts").mkdir(parents=True, exist_ok=True)
ok(f"{DATA_DIR} ready")

# Initialize database so first launch is instant
import sqlite3
for db_name, schemas in [
    ("metaops.db", [
        "CREATE TABLE IF NOT EXISTS app_states (app_name TEXT PRIMARY KEY, state TEXT NOT NULL, update_time REAL NOT NULL)",
        "CREATE TABLE IF NOT EXISTS user_states (app_name TEXT NOT NULL, user_id TEXT NOT NULL, state TEXT NOT NULL, update_time REAL NOT NULL, PRIMARY KEY (app_name, user_id))",
        "CREATE TABLE IF NOT EXISTS sessions (app_name TEXT NOT NULL, user_id TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL, create_time REAL NOT NULL, update_time REAL NOT NULL, PRIMARY KEY (app_name, user_id, id))",
        "CREATE TABLE IF NOT EXISTS events (id TEXT NOT NULL, app_name TEXT NOT NULL, user_id TEXT NOT NULL, session_id TEXT NOT NULL, invocation_id TEXT NOT NULL, timestamp REAL NOT NULL, event_data TEXT NOT NULL, PRIMARY KEY (app_name, user_id, session_id, id), FOREIGN KEY (app_name, user_id, session_id) REFERENCES sessions(app_name, user_id, id) ON DELETE CASCADE)",
        "CREATE TABLE IF NOT EXISTS skills (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL, instructions TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_review', version INTEGER NOT NULL DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS skill_resources (id INTEGER PRIMARY KEY AUTOINCREMENT, skill_name TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL, FOREIGN KEY (skill_name) REFERENCES skills(name) ON DELETE CASCADE, UNIQUE(skill_name, path))",
        "CREATE TABLE IF NOT EXISTS rag_sources (file_path TEXT PRIMARY KEY, filename TEXT NOT NULL, description TEXT, global_context TEXT, file_size INTEGER NOT NULL, chunk_count INTEGER NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS portkey_logs (id TEXT PRIMARY KEY, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, session_id TEXT, role TEXT, provider TEXT NOT NULL, model TEXT NOT NULL, prompt TEXT, completion TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER, cost REAL, latency_ms INTEGER, status_code INTEGER, error_message TEXT)",
        "CREATE TABLE IF NOT EXISTS subagent_logs (id TEXT PRIMARY KEY, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, session_id TEXT, parent_agent TEXT NOT NULL, subagent_name TEXT NOT NULL, query TEXT, response TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER, latency_ms INTEGER, status TEXT)",
    ]),
]:
    db_path = DATA_DIR / db_name
    if not db_path.exists():
        conn = sqlite3.connect(str(db_path))
        for stmt in schemas:
            conn.execute(stmt)
        conn.commit()
        conn.close()
        ok(f"{db_name} created")

# Initialize ChromaDB collections
chroma_dir = DATA_DIR / "metaops_vector_db"
chroma_dir.mkdir(parents=True, exist_ok=True)
try:
    import chromadb
    client = chromadb.PersistentClient(path=str(chroma_dir))
    for name in ["episodic_memory", "semantic_memory", "procedural_memory", "persona_memory"]:
        client.get_or_create_collection(name)
    ok("ChromaDB vector collections created")
except ImportError:
    ok(f"{chroma_dir} created (collections init at first launch)")
except Exception as e:
    warn(f"ChromaDB init failed: {e}")


# ── 7. .env setup ─────────────────────────────────────────────────────────────
header("7 / 7  Environment configuration")
if ENV_FILE.exists():
    ok(".env already exists — not overwritten")
elif ENV_EXAMPLE.exists():
    shutil.copy(ENV_EXAMPLE, ENV_FILE)
    warn(".env created from .env.example — fill in your API keys before starting")
else:
    warn("No .env found — create one from .env.example before starting")


# ── Shell detection ───────────────────────────────────────────────────────────
shell = shutil.which("bash") or shutil.which("powershell") or shutil.which("cmd")
if shell:
    ok(f"Shell detected: {shell}")
else:
    warn("No shell found (bash/powershell/cmd) — terminal tools will not work")


# ── Smoke test ────────────────────────────────────────────────────────────────
header("Smoke test")
info("Verifying install in a fresh Python process...")
r = subprocess.run(
    [sys.executable, "-c", "from metaops.config import MetaOpsConfig; MetaOpsConfig()"],
    capture_output=True, text=True, cwd=str(PROJECT_ROOT)
)
if r.returncode != 0:
    print(r.stderr.strip())
    fail("Import check failed — see errors above")
ok("MetaOps imports correctly")


# ── Done ──────────────────────────────────────────────────────────────────────
print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MetaOps is ready.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

  Location : {PROJECT_ROOT}

  Next steps:
  1. Fill in your API keys in  .env
  2. (Optional) Edit  mcp_servers.json  to add MCP servers
  3. Start:  python -m metaops.main

""")
