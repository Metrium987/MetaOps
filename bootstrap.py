"""
MetaOps — one-command bootstrap
curl -sSL https://raw.githubusercontent.com/Metrium987/MetaOps/main/bootstrap.py | python -
"""
import sys
import os
import shutil
import subprocess
from pathlib import Path

REPO_URL = "https://github.com/Metrium987/MetaOps.git"
REPO_DIR = "MetaOps"

# Force UTF-8 on Windows consoles
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


# ── Resolve project root ONCE ────────────────────────────────────────────────
# All paths derive from this — no relative paths after this point.
PROJECT_ROOT = Path.cwd() / REPO_DIR
VENV_DIR     = PROJECT_ROOT / ".venv"
DATA_DIR     = PROJECT_ROOT / ".data"
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
    fail(f"Python 3.10+ required — found {major}.{minor}. Download: https://python.org")
ok(f"Python {major}.{minor}  ({sys.executable})")


# ── 2. System dependencies ────────────────────────────────────────────────────
header("2 / 5  System dependencies")

# git
git = shutil.which("git")
if not git:
    info("git not found — installing automatically...")
    for pkg_mgr in ["apt-get", "apt", "dnf", "yum", "brew", "pacman", "zypper"]:
        if shutil.which(pkg_mgr):
            cmd = ["brew", "install", "git"] if pkg_mgr == "brew" else ["sudo", pkg_mgr, "install", "-y", "git"]
            info(f"Using {pkg_mgr}...")
            r = subprocess.run(cmd)
            if r.returncode == 0:
                git = shutil.which("git")
            break
    if not git:
        fail("git not found — install manually: sudo apt-get install -y git")
ok(f"git: {git}")

# Node.js / npx
npx = shutil.which("npx")
if not npx:
    info("npx not found — installing Node.js...")
    installed_node = False
    if sys.platform == "win32":
        for mgr, cmd in [
            ("winget", ["winget", "install", "--id", "OpenJS.NodeJS", "-e", "--silent"]),
            ("choco",  ["choco", "install", "nodejs", "-y"]),
        ]:
            if shutil.which(mgr):
                info(f"Using {mgr}...")
                r = subprocess.run(cmd)
                if r.returncode == 0:
                    installed_node = True
                break
    else:
        for pkg_mgr in ["apt-get", "apt", "dnf", "yum", "brew", "pacman", "zypper"]:
            if shutil.which(pkg_mgr):
                cmd = ["brew", "install", "node"] if pkg_mgr == "brew" else ["sudo", pkg_mgr, "install", "-y", "nodejs", "npm"]
                info(f"Using {pkg_mgr}...")
                r = subprocess.run(cmd)
                if r.returncode == 0:
                    installed_node = True
                break
    npx = shutil.which("npx")
    if npx:
        ok(f"npx: {npx}")
    elif installed_node:
        warn("Node.js installed but npx not in PATH yet — restart shell after install")
    else:
        warn("Could not install Node.js — MCP npm servers will use npx fallback at runtime")
else:
    ok(f"npx: {npx}")

# uv
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
    if not uv:
        warn("uv installer failed — falling back to pip")
        subprocess.run([sys.executable, "-m", "pip", "install", "uv", "--quiet"])
        uv = shutil.which("uv")

if uv and Path(uv).exists():
    ok(f"uv: {uv}")
else:
    fail("Could not install uv. Run: curl -LsSf https://astral.sh/uv/install.sh | sh")


# ── 3. Clone or update ────────────────────────────────────────────────────────
header("3 / 5  Repository")
if PROJECT_ROOT.exists():
    info(f"{REPO_DIR} exists — pulling latest...")
    r = subprocess.run(["git", "-C", str(PROJECT_ROOT), "pull", "--ff-only"], capture_output=True, text=True)
    if r.returncode != 0:
        warn(f"git pull failed — using existing files\n{r.stderr.strip()}")
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
    info(f"Creating venv at {VENV_DIR} ...")
    subprocess.run([uv, "venv", str(VENV_DIR), "--python", sys.executable], check=True)
    ok("Virtual environment created")
else:
    ok(f"Venv exists: {VENV_DIR}")

info("Installing MetaOps (uv pip install -e .) ...")
print()
r = subprocess.run([uv, "pip", "install", "-e", ".", "--python", str(VENV_PYTHON)], cwd=str(PROJECT_ROOT))
print()
if r.returncode != 0:
    fail("Installation failed — see errors above")
ok("MetaOps installed")

# MCP servers
npm = shutil.which("npm")
if npm:
    info("Installing MCP servers via npm ...")
    r = subprocess.run([npm, "install", "@modelcontextprotocol/server-filesystem", "@modelcontextprotocol/server-memory", "--quiet"], cwd=str(PROJECT_ROOT))
    if r.returncode == 0:
        ok("MCP servers installed locally")
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
ok(f"{DATA_DIR} ready")

# Initialize databases so first launch is instant
import sqlite3
for db_name, schema in [
    ("metaops_sessions.db", None),
    ("metaops_skills.db", """
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            trigger_pattern TEXT,
            procedure TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """),
]:
    db_path = DATA_DIR / db_name
    if not db_path.exists():
        conn = sqlite3.connect(str(db_path))
        if schema:
            conn.executescript(schema)
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
    # chromadb not in system Python — will init at first launch via venv
    ok(f"{chroma_dir} created (collections init at first launch)")
except Exception as e:
    warn(f"ChromaDB init failed: {e}")

# .env — always inside PROJECT_ROOT
if ENV_FILE.exists():
    ok(".env already exists")
elif ENV_EXAMPLE.exists():
    shutil.copy(ENV_EXAMPLE, ENV_FILE)
    warn(".env created from .env.example — add your API keys")
else:
    warn("No .env.example found — create .env manually")

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


# ── Done ──────────────────────────────────────────────────────────────────────
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

if not launcher_installed and sys.platform != "win32":
    print(f"  Tip: export PATH=\"{VENV_BIN}:$PATH\" >> ~/.bashrc && source ~/.bashrc\n")
