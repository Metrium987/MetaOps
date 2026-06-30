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

def ok(msg):     print(f"  {OK}    {msg}")
def fail(msg):   print(f"  {FAIL}  {msg}"); sys.exit(1)
def warn(msg):   print(f"  {WARN}  {msg}")
def info(msg):   print(f"  {INFO}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

def _run(*cmd, stream=True, check=False):
    """Run a command, optionally streaming output."""
    r = subprocess.run(cmd, capture_output=not stream)
    if check and r.returncode != 0:
        if not stream and r.stderr:
            print(r.stderr.decode(errors="replace")[-2000:])
        fail(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return r


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
            r = subprocess.run(["sudo", pkg_mgr, "install", "-y", "git"] if pkg_mgr != "brew" else ["brew", "install", "git"])
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

# uv
uv = shutil.which("uv")
if not uv:
    info("uv not found — installing (fast Python package manager)...")
    if sys.platform == "win32":
        # Windows: install via pip as fallback
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "uv", "--quiet"],
            capture_output=True,
        )
        uv = shutil.which("uv")
    else:
        # Linux/macOS: official installer
        r = subprocess.run(
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            shell=True,
        )
        # The installer puts uv in ~/.local/bin or ~/.cargo/bin — refresh PATH
        for candidate in [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]:
            if candidate.exists():
                uv = str(candidate)
                break
        if not uv:
            uv = shutil.which("uv")

    if not uv:
        # Final fallback: pip install uv
        warn("Official uv installer failed — trying pip install uv...")
        subprocess.run([sys.executable, "-m", "pip", "install", "uv", "--quiet"])
        uv = shutil.which("uv") or str(Path(sys.executable).parent / "uv")

if uv and Path(uv).exists():
    ok(f"uv: {uv}")
else:
    fail("Could not install uv. Run manually: curl -LsSf https://astral.sh/uv/install.sh | sh")


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

# Resolve absolute paths before any chdir
REPO_ABS = REPO_DIR.resolve()
VENV_ABS = REPO_ABS / ".venv"

if sys.platform == "win32":
    venv_python = VENV_ABS / "Scripts" / "python.exe"
    venv_bin    = VENV_ABS / "Scripts"
else:
    venv_python = VENV_ABS / "bin" / "python"
    venv_bin    = VENV_ABS / "bin"


# ── 4. Virtual environment + install (uv) ────────────────────────────────────
header("4 / 5  Installing MetaOps + dependencies  (uv)")

# Create venv with uv
if not VENV_ABS.exists():
    info(f"Creating venv at {VENV_ABS} ...")
    _run(uv, "venv", str(VENV_ABS), "--python", sys.executable, stream=False, check=True)
    ok("Virtual environment created")
else:
    ok(f"Virtual environment already exists: {VENV_ABS}")

# Install MetaOps — uv pip install streams output automatically
os.chdir(REPO_ABS)
info("uv pip install -e .  (downloading dependencies...)")
print()
r = subprocess.run([uv, "pip", "install", "-e", ".", "--python", str(venv_python)])
print()
if r.returncode != 0:
    fail("Installation failed — see errors above")
ok("MetaOps installed")

# Optional audit tools
for tool_bin, package in [("bandit", "bandit"), ("pip-audit", "pip-audit")]:
    if (venv_bin / tool_bin).exists() or (venv_bin / f"{tool_bin}.exe").exists():
        ok(f"{tool_bin} already available")
    else:
        info(f"Installing {package} (optional — audit workflow)...")
        r = subprocess.run(
            [uv, "pip", "install", package, "--python", str(venv_python)],
            capture_output=True,
        )
        if r.returncode == 0:
            ok(f"{tool_bin} installed")
        else:
            warn(f"{tool_bin} unavailable — audit scans will skip it")


# ── 5. First-run setup ────────────────────────────────────────────────────────
header("5 / 5  First-run setup")

Path("data/artifacts").mkdir(parents=True, exist_ok=True)
ok("./data/ directories created")

env     = Path(".env")
example = Path(".env.example")
if env.exists():
    ok(".env already exists")
elif example.exists():
    shutil.copy(example, env)
    warn(".env created from .env.example — add your API keys before starting")
else:
    warn("No .env.example — create .env manually before starting")

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
            launcher.write_text(f"#!/bin/sh\nexec {venv_metaops.resolve()} \"$@\"\n")
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
  1. Edit .env — add at minimum your provider API key
  2. {metaops_cmd}
""")

if not launcher_installed and sys.platform != "win32":
    print(f"  Tip: add the venv to your PATH to use 'metaops' anywhere:")
    print(f"  echo 'export PATH=\"{venv_bin}:$PATH\"' >> ~/.bashrc && source ~/.bashrc\n")
