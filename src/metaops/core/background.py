import glob
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from google.adk.agents import Agent
from google.adk.workflow import Workflow
from google.adk.tools import FunctionTool
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from metaops.config import MetaOpsConfig

logger = logging.getLogger(__name__)
config = MetaOpsConfig()

# ---------------------------------------------------------------------------
# Audit tools — each one is a bounded, deterministic operation
# ---------------------------------------------------------------------------

_IGNORE_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", "dist", "build", ".mypy_cache"}


def list_source_files(directory: str = "src", extensions: str = ".py,.ts,.js") -> dict:
    """List all source files in a directory tree for audit."""
    ext_list = [e.strip() for e in extensions.split(",")]
    files: list[str] = []
    for ext in ext_list:
        files.extend(glob.glob(f"{directory}/**/*{ext}", recursive=True))
    filtered = [
        f for f in files
        if not any(ig in Path(f).parts for ig in _IGNORE_DIRS)
    ]
    return {"files": sorted(filtered)[:150], "total": len(filtered)}


def read_source_file(path: str) -> dict:
    """Read a source file for code quality analysis."""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        return {"path": path, "content": content[:8000], "lines": content.count("\n")}
    except Exception as exc:
        return {"path": path, "error": str(exc)}


def read_project_config() -> dict:
    """Read project configuration files to audit dependencies and settings."""
    result: dict[str, str] = {}
    for fname in ["pyproject.toml", "setup.py", "requirements.txt", "package.json"]:
        p = Path(fname)
        if p.exists():
            result[fname] = p.read_text(encoding="utf-8", errors="replace")[:4000]
    return result if result else {"error": "No project config files found"}


def run_static_analysis(tool: str, target: str = ".") -> dict:
    """Run a static analysis or security tool.

    Supported tools:
      bandit    — Python security linter (OWASP checks)
      pip-audit — Python dependency vulnerability scanner
      pylint    — Python code quality checker
    """
    valid = {"bandit", "pip-audit", "pylint"}
    if tool not in valid:
        return {"error": f"Unknown tool '{tool}'. Valid: {valid}"}

    cmd_map: dict[str, list[str]] = {
        "bandit":    ["bandit", "-r", target, "-f", "json", "-ll", "--quiet"],
        "pip-audit": ["pip-audit", "--format=json", "--progress-spinner=off"],
        "pylint":    ["pylint", target, "--output-format=json", "--score=no", "--disable=C,R"],
    }
    try:
        proc = subprocess.run(
            cmd_map[tool],
            capture_output=True,
            text=True,
            timeout=90,
        )
        output = proc.stdout or proc.stderr
        return {
            "tool": tool,
            "exit_code": proc.returncode,
            "output": output[:6000],
        }
    except FileNotFoundError:
        return {"tool": tool, "status": "not_installed", "output": f"{tool} not found — skipping"}
    except subprocess.TimeoutExpired:
        return {"tool": tool, "status": "timeout", "output": f"{tool} timed out after 90s"}
    except Exception as exc:
        return {"tool": tool, "error": str(exc)}


def check_env_secrets() -> dict:
    """Scan source files for potential hardcoded secrets and dangerous patterns."""
    dangerous_patterns = [
        ("hardcoded_key",    r'(api_key|secret|password|token)\s*=\s*["\'][^"\']{8,}["\']'),
        ("eval_usage",       r'\beval\s*\('),
        ("exec_usage",       r'\bexec\s*\('),
        ("os_system",        r'\bos\.system\s*\('),
        ("shell_true",       r'shell\s*=\s*True'),
        ("bare_except",      r'except\s*:'),
        ("print_debug",      r'\bprint\s*\('),
        ("TODO_FIXME",       r'(TODO|FIXME|HACK|XXX)'),
        ("placeholder",      r'(pass\s*#|raise NotImplementedError|\.\.\.)\s*$'),
    ]
    import re
    findings: list[dict] = []
    py_files = glob.glob("src/**/*.py", recursive=True)
    py_files = [f for f in py_files if not any(ig in Path(f).parts for ig in _IGNORE_DIRS)]

    for fpath in py_files[:80]:
        try:
            content = Path(fpath).read_text(encoding="utf-8", errors="replace")
            for category, pattern in dangerous_patterns:
                for m in re.finditer(pattern, content, re.IGNORECASE | re.MULTILINE):
                    line_no = content[:m.start()].count("\n") + 1
                    findings.append({
                        "file": fpath,
                        "line": line_no,
                        "category": category,
                        "snippet": content[m.start():m.start()+80].strip(),
                    })
        except Exception:
            continue

    return {
        "findings": findings[:200],
        "total": len(findings),
        "files_scanned": len(py_files),
    }


list_files_tool     = FunctionTool(func=list_source_files)
read_file_tool      = FunctionTool(func=read_source_file)
project_config_tool = FunctionTool(func=read_project_config)
static_analysis_tool = FunctionTool(func=run_static_analysis)
env_secrets_tool    = FunctionTool(func=check_env_secrets)

# ---------------------------------------------------------------------------
# Auditor agent
# ---------------------------------------------------------------------------

_AUDITOR_INSTRUCTION = f"""You are a senior security and software quality auditor. Today: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}.

Perform a thorough audit in this exact sequence:

1. list_source_files — get the full inventory
2. read_project_config — review dependencies and settings
3. check_env_secrets — scan for security patterns across all files
4. run_static_analysis(tool="bandit") — OWASP security scan
5. run_static_analysis(tool="pip-audit") — dependency CVE scan
6. read_source_file on the 5-10 most critical files (config.py, root.py, tools/, core/)
7. Synthesize all findings into a structured report

Audit dimensions:
- SECURITY: secrets exposure, injection risks, unsafe function usage, shell=True, eval/exec
- DEPENDENCIES: CVE-flagged packages, outdated pinning, missing packages in pyproject.toml
- CODE QUALITY: bare excepts, missing error handling, TODO/FIXME count, placeholder stubs
- ARCHITECTURE: dead imports, missing __init__.py, inconsistent naming, circular dependencies
- COMPLETENESS: unimplemented functions, missing tests for critical paths, placeholder workflows

Output this exact structure:

## Audit Report — {{timestamp}}

### Critical Issues (block production)
- [SEVERITY] File:line — description + fix recommendation

### Warnings (fix soon)
- [WARNING] File:line — description

### Dependency Vulnerabilities
- Package@version — CVE-ID — impact

### Code Quality Metrics
- Files scanned: N
- Security findings: N
- TODO/FIXME count: N
- Bare excepts: N

### Architecture Notes
- [observations about structure, missing pieces]

### Overall Health
Score: X/10
Verdict: [one sentence]
Priority fixes: [top 3 actions to take now]"""

code_auditor_agent = Agent(
    name="code_auditor",
    model=config.auditor.to_litellm(),
    instruction=_AUDITOR_INSTRUCTION,
    tools=[
        list_files_tool,
        read_file_tool,
        project_config_tool,
        static_analysis_tool,
        env_secrets_tool,
    ],
)

# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

background_audit_workflow = Workflow(
    name="background_audit",
    edges=[("START", code_auditor_agent)],
)

background_runner = Runner(
    node=background_audit_workflow,
    app_name="metaops_background",
    session_service=InMemorySessionService(),
)

# ---------------------------------------------------------------------------
# On-demand FunctionTool so the coordinator can trigger audits
# ---------------------------------------------------------------------------

async def run_audit(scope: str = "full") -> dict:
    """Run a deep code and security audit of the project.

    Scans source files, runs bandit (security) and pip-audit (CVEs),
    and produces a structured report covering security, dependencies,
    code quality, and architecture.

    Args:
        scope: "full" for complete audit, "security" for security/CVEs only,
               "quality" for code quality only.

    Returns:
        dict with keys:
            report   — full audit report in markdown
            scope    — echo of requested scope
    """
    import uuid
    from google.genai import types as gtypes

    prompt = f"Run a {scope} audit of this project."
    session = await background_runner.session_service.create_session(
        app_name="metaops_background",
        user_id="auditor",
        session_id=str(uuid.uuid4()),
    )
    parts: list[str] = []
    async for event in background_runner.run_async(
        user_id="auditor",
        session_id=session.id,
        new_message=gtypes.Content(parts=[gtypes.Part(text=prompt)]),
    ):
        if event.content:
            for part in event.content.parts or []:
                if part.text:
                    parts.append(part.text)

    return {"report": "\n".join(parts), "scope": scope}


audit_tool = FunctionTool(func=run_audit)
