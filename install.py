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
DATA_DIR     = PROJECT_ROOT / "data"
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
        [npm, "install", "@modelcontextprotocol/server-filesystem", "@modelcontextprotocol/server-memory", "--quiet"],
        cwd=str(PROJECT_ROOT)
    )
    if r.returncode == 0:
        ok("MCP servers installed locally")
    else:
        warn("Failed to install local MCP servers via npm — npx fallback will be used at runtime")
else:
    warn("npm not found — skipping local MCP server installation")


# ── 6. Data directories ───────────────────────────────────────────────────────
header("6 / 7  Data directories")
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "artifacts").mkdir(parents=True, exist_ok=True)
ok(f"{DATA_DIR} ready")


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
