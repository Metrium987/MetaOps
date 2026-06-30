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
VENV_DIR  = REPO_DIR / ".venv"

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

def _apt_install(*packages):
    r = subprocess.run(
        ["sudo", "apt-get", "install", "-y", *packages],
        capture_output=False,
    )
    return r.returncode == 0


header("MetaOps — Setup")
print(f"  {REPO_URL}\n")


# ── 1. Python 3.10+ ───────────────────────────────────────────────────────────
header("1 / 5  Python version")
major, minor = sys.version_info[:2]
if (major, minor) < (3, 10):
    fail(f"Python 3.10+ required — found {major}.{minor}. Download: https://python.org")
ok(f"Python {major}.{minor}  ({sys.executable})")


# ── 2. System dependencies (git, venv, pip) ───────────────────────────────────
header("2 / 5  System dependencies")

# git
git = shutil.which("git")
if not git:
    info("git not found — installing automatically...")
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
            info(f"Using {pkg_mgr}...")
            r = subprocess.run(cmd, capture_output=False)
            if r.returncode == 0:
                git = shutil.which("git")
            break
    if not git:
        fail(
            "git not found.\n"
            "  Debian/Ubuntu/Raspberry Pi: sudo apt-get install -y git\n"
            "  Fedora/RHEL:                sudo dnf install -y git\n"
            "  macOS:                      brew install git\n"
            "  Windows:                    https://git-scm.com"
        )
ok(f"git: {git}")

# venv module (Debian splits it out as python3-venv)
r = subprocess.run(
    [sys.executable, "-m", "venv", "--help"],
    capture_output=True,
)
if r.returncode != 0:
    info("python venv module missing — installing python3-venv...")
    py_ver = f"python{major}.{minor}"
    if shutil.which("apt-get"):
        ok_apt = _apt_install(f"{py_ver}-venv") or _apt_install("python3-venv")
        if not ok_apt:
            fail("Could not install python3-venv. Run: sudo apt-get install python3-venv")
    else:
        fail("venv module not found. Install the python3-venv package for your distro.")
ok("venv module available")


# ── 3. Clone or update ────────────────────────────────────────────────────────
header("3 / 5  Repository")
if REPO_DIR.exists():
    info(f"{REPO_DIR} already exists — pulling latest...")
    r = subprocess.run(
        ["git", "-C", str(REPO_DIR), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        warn(f"git pull failed — using existing files\n{r.stderr.strip()}")
    else:
        ok("Repository updated")
else:
    info(f"Cloning into ./{REPO_DIR} ...")
    r = subprocess.run(
        ["git", "clone", REPO_URL, str(REPO_DIR)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr)
        fail("git clone failed")
    ok("Repository cloned")


# ── 4. Virtual environment + install ─────────────────────────────────────────
header("4 / 5  Installing MetaOps + dependencies")

# Resolve REPO_DIR to absolute BEFORE any chdir
REPO_ABS = REPO_DIR.resolve()
VENV_ABS = REPO_ABS / ".venv"

# Create venv if needed
if not VENV_ABS.exists():
    info(f"Creating virtual environment at {VENV_ABS} ...")
    r = subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_ABS)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr)
        fail("Failed to create virtual environment")
    ok("Virtual environment created")
else:
    ok(f"Virtual environment already exists: {VENV_ABS}")

# Resolve venv executables (absolute paths — safe across chdir)
if sys.platform == "win32":
    venv_python = VENV_ABS / "Scripts" / "python.exe"
    venv_bin    = VENV_ABS / "Scripts"
else:
    venv_python = VENV_ABS / "bin" / "python"
    venv_bin    = VENV_ABS / "bin"

# Upgrade pip inside venv (handles Debian's outdated bundled pip)
info("Upgrading pip inside venv...")
subprocess.run(
    [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
    capture_output=True,
)

# Install MetaOps
info("pip install -e .")
os.chdir(REPO_ABS)
r = subprocess.run(
    [str(venv_python), "-m", "pip", "install", "-e", ".", "--quiet"],
    capture_output=True, text=True,
)
if r.returncode != 0:
    print(r.stderr[-3000:])
    fail("pip install failed")
ok("MetaOps installed in venv")

# Optional audit tools
for tool_bin, package in [("bandit", "bandit"), ("pip-audit", "pip-audit")]:
    if (venv_bin / tool_bin).exists() or (venv_bin / f"{tool_bin}.exe").exists():
        ok(f"{tool_bin} already available")
    else:
        info(f"Installing {package} (optional — audit workflow)...")
        r = subprocess.run(
            [str(venv_python), "-m", "pip", "install", package, "--quiet"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            ok(f"{tool_bin} installed")
        else:
            warn(f"{tool_bin} unavailable — audit scans will skip it")


# ── 5. First-run setup ────────────────────────────────────────────────────────
header("5 / 5  First-run setup")

# data directories
Path("data/artifacts").mkdir(parents=True, exist_ok=True)
ok("./data/ directories created")

# .env
env     = Path(".env")
example = Path(".env.example")
if env.exists():
    ok(".env already exists")
elif example.exists():
    shutil.copy(example, env)
    warn(".env created from .env.example — add your API keys before starting")
else:
    warn("No .env.example — create .env manually before starting")

# Shell check
shell = shutil.which("bash") or shutil.which("powershell") or shutil.which("cmd")
if shell:
    ok(f"Shell: {shell}")
else:
    warn("No shell found — terminal tools may not work")

# Global `metaops` launcher
venv_metaops = venv_bin / ("metaops.exe" if sys.platform == "win32" else "metaops")
launcher_installed = False
if sys.platform != "win32" and venv_metaops.exists():
    for launcher_dir in [Path.home() / ".local" / "bin", Path("/usr/local/bin")]:
        try:
            launcher_dir.mkdir(parents=True, exist_ok=True)
            launcher = launcher_dir / "metaops"
            launcher.write_text(
                f"#!/bin/sh\nexec {venv_metaops.resolve()} \"$@\"\n"
            )
            launcher.chmod(0o755)
            ok(f"Global launcher installed: {launcher}")
            launcher_installed = True
            break
        except PermissionError:
            continue

# Smoke test
info("Verifying install...")
r = subprocess.run(
    [str(venv_python), "-c",
     "from metaops.config import MetaOpsConfig; MetaOpsConfig()"],
    capture_output=True, text=True,
)
if r.returncode != 0:
    warn(f"Import check:\n{r.stderr.strip()}")
else:
    ok("MetaOps imports correctly")


# ── Done ──────────────────────────────────────────────────────────────────────
cwd = Path.cwd()
metaops_cmd = "metaops" if launcher_installed else str(venv_metaops)

print(f"""
{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MetaOps is ready.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}

  Location : {cwd}
  Venv     : {VENV_ABS}

  Next steps:
  1. Edit .env — add at minimum OPENROUTER_API_KEY (or any provider key)
  2. {metaops_cmd}
""")

if not launcher_installed and sys.platform != "win32":
    print(f"  Tip: add the venv to your PATH to use 'metaops' anywhere:")
    print(f"  echo 'export PATH=\"{venv_bin}:$PATH\"' >> ~/.bashrc && source ~/.bashrc\n")
