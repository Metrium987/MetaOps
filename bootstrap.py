"""
MetaOps — one-command bootstrap
curl -sSL https://raw.githubusercontent.com/Metrium987/MetaOps/main/bootstrap.py | python -
"""
import sys
import os
import shutil
import subprocess
import sqlite3
from pathlib import Path

REPO_URL = "https://github.com/Metrium987/MetaOps.git"
REPO_DIR = "MetaOps"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BOLD  = "\033[1m"
OK    = "\033[92m[OK]\033[0m"
FAIL  = "\033[91m[FAIL]\033[0m"
WARN  = "\033[93m[WARN]\033[0m"
INFO  = "\033[94m[-->]\033[0m"
RESET = "\033[0m"

def ok(msg):     print(f"  {OK}    {msg}")
def fail(msg):   print(f"  {FAIL}  {msg}"); sys.exit(1)
def warn(msg):   print(f"  {WARN}  {msg}")
def info(msg):   print(f"  {INFO}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")


PROJECT_ROOT = Path.cwd() / REPO_DIR
DATA_DIR     = PROJECT_ROOT / ".data"
VENV_DIR     = PROJECT_ROOT / ".venv"
ENV_FILE     = PROJECT_ROOT / ".env"
ENV_EXAMPLE  = PROJECT_ROOT / ".env.example"

if sys.platform == "win32":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
    VENV_BIN    = VENV_DIR / "Scripts"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"
    VENV_BIN    = VENV_DIR / "bin"


header("MetaOps — Setup")
print(f"  {REPO_URL}\n")


# ── 1. Python 3.10+ ───────────────────────────────────────────────────────────
header("1 / 5  Python version")
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    fail(f"Python 3.10+ required — found {major}.{minor}")
ok(f"Python {major}.{minor}  ({sys.executable})")


# ── 2. System dependencies ────────────────────────────────────────────────────
header("2 / 5  System dependencies")

git = shutil.which("git")
if not git:
    info("git not found — installing...")
    for pkg_mgr in ["apt-get", "apt", "dnf", "yum", "brew", "pacman", "zypper"]:
        if shutil.which(pkg_mgr):
            cmd = ["brew", "install", "git"] if pkg_mgr == "brew" else ["sudo", pkg_mgr, "install", "-y", "git"]
            subprocess.run(cmd)
            git = shutil.which("git")
            break
    if not git:
        fail("git not found — install manually: sudo apt-get install -y git")
ok(f"git: {git}")

npx = shutil.which("npx")
if not npx:
    info("npx not found — installing Node.js...")
    if sys.platform == "win32":
        for mgr, cmd in [("winget", ["winget", "install", "--id", "OpenJS.NodeJS", "-e", "--silent"]), ("choco", ["choco", "install", "nodejs", "-y"])]:
            if shutil.which(mgr):
                subprocess.run(cmd)
                break
    else:
        for pkg_mgr in ["apt-get", "apt", "dnf", "yum", "brew", "pacman", "zypper"]:
            if shutil.which(pkg_mgr):
                cmd = ["brew", "install", "node"] if pkg_mgr == "brew" else ["sudo", pkg_mgr, "install", "-y", "nodejs", "npm"]
                subprocess.run(cmd)
                break
    npx = shutil.which("npx")
if npx:
    ok(f"npx: {npx}")
else:
    warn("npx not found — MCP servers will not work")

uv = shutil.which("uv")
if not uv:
    info("uv not found — installing...")
    if sys.platform == "win32":
        subprocess.run([sys.executable, "-m", "pip", "install", "uv", "--quiet"], capture_output=True)
        uv = shutil.which("uv")
    else:
        subprocess.run("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True)
        for candidate in [Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"]:
            if candidate.exists():
                uv = str(candidate)
                break
        if not uv:
            uv = shutil.which("uv")
if uv and Path(uv).exists():
    ok(f"uv: {uv}")
else:
    fail("Could not install uv")


# ── 3. Clone or update ────────────────────────────────────────────────────────
header("3 / 5  Repository")
if PROJECT_ROOT.exists():
    info(f"{REPO_DIR} exists — pulling latest...")
    r = subprocess.run(["git", "-C", str(PROJECT_ROOT), "pull", "--ff-only"], capture_output=True, text=True)
    if r.returncode != 0:
        warn(f"git pull failed — using existing\n{r.stderr.strip()}")
    else:
        ok("Repository updated")
else:
    info(f"Cloning into {PROJECT_ROOT} ...")
    r = subprocess.run(["git", "clone", REPO_URL, str(PROJECT_ROOT)], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        fail("git clone failed")
    ok("Repository cloned")


# ── 4. Virtual environment + install ──────────────────────────────────────────
header("4 / 5  Installing MetaOps + dependencies")

if not VENV_DIR.exists():
    info(f"Creating venv ...")
    subprocess.run([uv, "venv", str(VENV_DIR), "--python", sys.executable], check=True)
    ok("Virtual environment created")
else:
    ok(f"Venv exists")

info("Installing MetaOps ...")
print()
r = subprocess.run([uv, "pip", "install", "-e", ".", "--python", str(VENV_PYTHON)], cwd=str(PROJECT_ROOT))
print()
if r.returncode != 0:
    fail("Installation failed")
ok("MetaOps installed")

# MCP servers — install locally so npx finds them without downloading each time
npm = shutil.which("npm")
if npm:
    info("Installing MCP servers via npm ...")
    r = subprocess.run([npm, "install", "@modelcontextprotocol/server-filesystem", "@modelcontextprotocol/server-memory", "@portkey-ai/gateway", "--save"], cwd=str(PROJECT_ROOT))
    if r.returncode == 0:
        ok("MCP servers and Portkey gateway installed locally")
    else:
        warn("npm install failed — npx fallback at runtime")

# Audit tools (optional)
for tool_bin, package in [("bandit", "bandit"), ("pip-audit", "pip-audit")]:
    if not ((VENV_BIN / tool_bin).exists() or (VENV_BIN / f"{tool_bin}.exe").exists()):
        info(f"Installing {package} (optional)...")
        subprocess.run([uv, "pip", "install", package, "--python", str(VENV_PYTHON)], capture_output=True)


# ── 5. First-run setup ────────────────────────────────────────────────────────
header("5 / 5  First-run setup")

DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "artifacts").mkdir(parents=True, exist_ok=True)
(DATA_DIR / "metaops_vector_db").mkdir(parents=True, exist_ok=True)
ok(f"{DATA_DIR} ready")

# Create unified SQLite database
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
        try:
            conn = sqlite3.connect(str(db_path))
            for stmt in schemas:
                conn.execute(stmt)
            conn.commit()
            conn.close()
            ok(f"{db_name} created")
        except Exception as e:
            warn(f"Failed to create {db_name}: {e}")
    else:
        ok(f"{db_name} exists")

# .env
if ENV_FILE.exists():
    ok(".env exists")
elif ENV_EXAMPLE.exists():
    shutil.copy(ENV_EXAMPLE, ENV_FILE)
    warn(".env created from .env.example — add your API keys")

# Shell
shell = shutil.which("bash") or shutil.which("powershell") or shutil.which("cmd")
ok(f"Shell: {shell}") if shell else warn("No shell found")

# Global launcher (Linux/macOS)
launcher_installed = False
if sys.platform != "win32":
    venv_metaops = VENV_BIN / "metaops"
    if venv_metaops.exists():
        for launcher_dir in [Path.home() / ".local" / "bin", Path("/usr/local/bin")]:
            try:
                launcher_dir.mkdir(parents=True, exist_ok=True)
                launcher = launcher_dir / "metaops"
                launcher.write_text(f"#!/bin/sh\nexec {venv_metaops.resolve()} \"$@\"\n")
                launcher.chmod(0o755)
                ok(f"Global launcher: {launcher}")
                launcher_installed = True
                break
            except PermissionError:
                continue

# Smoke test
info("Verifying install...")
r = subprocess.run([str(VENV_PYTHON), "-c", "from metaops.config import MetaOpsConfig; MetaOpsConfig()"], capture_output=True, text=True)
if r.returncode != 0:
    warn(f"Import check failed:\n{r.stderr.strip()}")
else:
    ok("MetaOps imports correctly")


metaops_cmd = "metaops" if launcher_installed else str(VENV_BIN / "metaops")

print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MetaOps is ready.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

  Location : {PROJECT_ROOT}
  Venv     : {VENV_DIR}

  Next steps:
  1. cd {PROJECT_ROOT}
  2. Edit .env — add your API keys
  3. {metaops_cmd}
""")
