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
REPO_DIR  = Path("MetaOps")

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

def ok(msg):   print(f"  {OK}    {msg}")
def fail(msg): print(f"  {FAIL}  {msg}"); sys.exit(1)
def warn(msg): print(f"  {WARN}  {msg}")
def info(msg): print(f"  {INFO}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")


header("MetaOps — Setup")
print(f"  {REPO_URL}\n")


# ── 1. Python 3.10+ ───────────────────────────────────────────────────────────
header("1 / 5  Python version")
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    fail(f"Python 3.10+ required — found {major}.{minor}. Download: https://python.org")
ok(f"Python {major}.{minor}  ({sys.executable})")


# ── 2. Git ────────────────────────────────────────────────────────────────────
header("2 / 5  Git")
git = shutil.which("git")
if not git:
    info("git not found — attempting automatic install...")
    installed = False
    if sys.platform != "win32":
        # Detect package manager and install git
        for pkg_mgr, cmd in [
            ("apt-get", ["sudo", "apt-get", "install", "-y", "git"]),
            ("apt",     ["sudo", "apt",     "install", "-y", "git"]),
            ("dnf",     ["sudo", "dnf",     "install", "-y", "git"]),
            ("yum",     ["sudo", "yum",     "install", "-y", "git"]),
            ("brew",    ["brew", "install",             "git"]),
            ("pacman",  ["sudo", "pacman",  "-S", "--noconfirm", "git"]),
            ("zypper",  ["sudo", "zypper",  "install", "-y", "git"]),
        ]:
            if shutil.which(pkg_mgr):
                info(f"Using {pkg_mgr} to install git...")
                r = subprocess.run(cmd, capture_output=False)
                if r.returncode == 0:
                    git = shutil.which("git")
                    installed = bool(git)
                break
    if not git:
        fail(
            "git not found and automatic install failed.\n"
            "  On Debian/Ubuntu/Raspberry Pi:  sudo apt-get install -y git\n"
            "  On Fedora/RHEL:                 sudo dnf install -y git\n"
            "  On macOS:                        brew install git\n"
            "  On Windows:                     https://git-scm.com"
        )
    if installed:
        ok(f"git installed automatically: {git}")
ok(git)


# ── 3. Clone or update ────────────────────────────────────────────────────────
header("3 / 5  Repository")
if REPO_DIR.exists():
    info(f"{REPO_DIR} already exists — pulling latest...")
    r = subprocess.run(["git", "-C", str(REPO_DIR), "pull", "--ff-only"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        warn(f"git pull failed — using existing files\n{r.stderr.strip()}")
    else:
        ok("Repository updated")
else:
    info(f"Cloning into ./{REPO_DIR} ...")
    r = subprocess.run(["git", "clone", REPO_URL, str(REPO_DIR)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        fail("git clone failed")
    ok("Repository cloned")

os.chdir(REPO_DIR)


# ── 4. Install package + dependencies ─────────────────────────────────────────
header("4 / 5  Installing MetaOps + dependencies")
info("pip install -e .")
r = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
    capture_output=True, text=True
)
if r.returncode != 0:
    print(r.stderr[-3000:])
    fail("pip install failed")
ok("Package installed")

for tool, package in [("bandit", "bandit"), ("pip-audit", "pip-audit")]:
    if shutil.which(tool):
        ok(f"{tool} already available")
    else:
        info(f"Installing {package} (optional — audit workflow)...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "--quiet"],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            ok(f"{tool} installed")
        else:
            warn(f"{tool} unavailable — audit scans will skip it")


# ── 5. First-run setup ────────────────────────────────────────────────────────
header("5 / 5  First-run setup")

# data directories
for d in ["data/artifacts"]:
    Path(d).mkdir(parents=True, exist_ok=True)
ok("./data/  directories created")

# .env
env     = Path(".env")
example = Path(".env.example")
if env.exists():
    ok(".env already exists")
elif example.exists():
    shutil.copy(example, env)
    warn(".env created from .env.example — edit it and add your API keys")
else:
    warn("No .env.example found — create .env manually before starting")

# shell check
shell = shutil.which("bash") or shutil.which("powershell") or shutil.which("cmd")
if shell:
    ok(f"Shell: {shell}")
else:
    warn("No shell found (bash/powershell/cmd) — terminal tools may not work")

# smoke test
info("Verifying install...")
r = subprocess.run(
    [sys.executable, "-c", "from metaops.config import MetaOpsConfig; MetaOpsConfig()"],
    capture_output=True, text=True
)
if r.returncode != 0:
    warn(f"Import check failed:\n{r.stderr.strip()}")
else:
    ok("MetaOps imports correctly")


# ── Done ──────────────────────────────────────────────────────────────────────
cwd = Path.cwd()
print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MetaOps is ready.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

  Location : {cwd}

  Next steps:
  1. cd {cwd}
  2. Edit .env  — add at minimum OPENROUTER_API_KEY (or any provider key)
  3. python -m metaops.main

""")
