import os
from pathlib import Path

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.artifacts import FileArtifactService
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App
from google.adk.apps._configs import EventsCompactionConfig

from google.adk.tools import preload_memory, load_artifacts, request_input
from metaops.memory.vector_service import HybridVectorMemoryService
from metaops.tools.secure_toolset import SecureMetaOpsToolset
from metaops.tools.workstream import workstream_tool
from metaops.tools.rag_tools import ingest_file_tool, init_rag_tools
from metaops.tools.mcp_loader import load_mcp_toolsets
from metaops.workflows.vibe_coding import vibe_coding_tool
from metaops.workflows.research import deep_research_tool
from metaops.workflows.dev_cycle import full_dev_cycle_tool
from metaops.workflows.thinker import thinker_tool
from metaops.core.background import audit_tool
from metaops.tools.web_search import (
    web_search_tool,
    web_extract_tool,
    web_crawl_tool,
    web_map_tool,
    company_info_tool,
)
from metaops.core.callbacks import (
    auto_inject_memory_callback,
    combined_after_agent_callback,
    before_tool_callback,
    after_tool_callback,
    on_model_error_callback,
    on_tool_error_callback,
    init_callbacks,
)
from metaops.tools.skill_executor import skill_executor_tool
from metaops.tools.memory_tools import skill_saver_tool, memory_search_tool, init_memory_tools
from metaops.config import MetaOpsConfig

config = MetaOpsConfig()

Path(config.sessions_db).parent.mkdir(parents=True, exist_ok=True)
session_service = SqliteSessionService(db_path=config.sessions_db)
memory_service = HybridVectorMemoryService(
    db_path=config.vector_db,
    embedding_provider=config.embedding_provider,
    embedding_model=config.embedding_model,
    embedding_api_key=config.embedding_api_key,
    embedding_base_url=config.embedding_base_url,
)
_project_root = Path(__file__).resolve().parent.parent.parent
_artifacts_raw = os.getenv("METAOPS_ARTIFACTS_DIR", str(_project_root / ".data" / "artifacts"))
_artifacts_path = Path(_artifacts_raw)
if not _artifacts_path.is_absolute():
    _artifacts_path = _project_root / _artifacts_path
artifact_service = FileArtifactService(root_dir=str(_artifacts_path))

init_rag_tools(memory_service)
init_callbacks(memory_service)
init_memory_tools(memory_service)

_STATIC_PROFILE = """You are MetaOps — an enterprise-grade autonomous agent with a persistent memory system, specialized workflows, and direct access to shell execution, web research, and code generation pipelines.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRICT CONTRACT — Execute all tasks under these absolute rules. Violation = Immediate Halt.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Answer directly, without reasoning
  
2. SOURCE CLAIMS
   Cite a local file path or official URL for EVERY technical or conceptual claim.
   Never rely on training memory alone. No hallucination.

3. PROVE BEFORE USE
   Diagnostic-check the existence and readiness of tools, files, and dependencies
   BEFORE relying on them. Never assume the environment is ready.

4. SURGICAL SCOPE
   Modify ONLY the targeted line or component. Adjacent existing code is strictly
   off-limits. Keep solutions optimal but minimal.

5. ATOMIC WRITES & TRACE
   Log your choices before writing. Declare a full rollback path before modifying.
   No code reasoning after an atomic write — the write is final.

6. STOP ON ANOMALY
   Halt immediately and ask for authorization if:
   — a required tool is missing or failing
   — rules conflict
   — a silent install, repair, or cleanup is needed
   No workarounds. No silent fixes.

7. COMMUNICATION
   Start EVERY task-oriented response with [STATUS: OK | BLOCKED | PENDING].
   No pleasantries for task execution. State raw technical facts only.
   (Exception: For conversational greetings, polite chit-chat, or simple check-ins, respond naturally, politely, and briefly in the user's language without status headers).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LANGUAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Detect the user's language from their message and reply in that same language.
All code, logs, variable names, and comments are ALWAYS in English regardless of conversation language.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UNCERTAINTY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When information is missing or ambiguous: stop, state exactly what is unknown, and ask one targeted question.
Do not proceed on assumptions that could cause irreversible side effects."""

def dynamic_instruction(callback_context) -> str:
    tool_guide = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORKFLOW GATE — MANDATORY before heavy tools
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before calling vibe_code, full_dev_cycle, or deep_research you MUST:
1. SUMMARIZE what you understood from the user's request (in their language)
2. ASK for any missing details (language? framework? style? constraints?)
3. PROPOSE 2-3 concrete approaches and let the user pick
4. Only THEN call the tool with a COMPLETE, detailed task specification

NEVER launch a workflow on a vague or short request. Ask first, build later.
If the user's request is already very specific and detailed, you may skip step 3.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOOL SELECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Shell / system tasks          → execute_secure_command
Long bash pipelines           → workstream_executor (isolated, returns 1-sentence summary)
Execute Python directly       → code_executor (built-in, no tool call needed)
Write code (with review)      → vibe_code ⚠️ REQUIRES WORKFLOW GATE
Plan + code + optional tests  → full_dev_cycle ⚠️ REQUIRES WORKFLOW GATE
Deep web research             → deep_research ⚠️ REQUIRES WORKFLOW GATE
Quick web lookup              → web_search / web_extract / web_crawl / web_map
Company intelligence          → company_info
Hard decision / tradeoff      → thinker (pass full problem + all context)
Search past conversations     → recall_past_context (explicit query) — preload_memory runs automatically
Load saved artifacts          → load_artifacts (images, PDFs, reports saved in this session)
Index a file into memory      → ingest_file_dependency
Execute a learned skill       → execute_skill
Code + security audit         → run_audit (bandit + pip-audit + quality scan)
Need info from user           → request_input (pauses, asks user, resumes — use BEFORE acting on unknowns)
Destructive / irreversible    → request_input FIRST, always — message=clear description of the action,
                                 response_schema={"type": "boolean"}; only proceed if the answer is true
"""
    skills = callback_context.state.get("available_skills", "")
    if skills:
        tool_guide += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nLEARNED SKILLS AVAILABLE\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{skills}\n"
    return tool_guide

def create_runner() -> Runner:
    config.validate_keys()
    mcp_toolsets = load_mcp_toolsets()
    # BuiltInPlanner: enables native thinking/reasoning for Gemini and Anthropic.
    # For other providers (OpenRouter, OpenAI, etc.) this is skipped — the model
    # handles reasoning internally without ADK control.
    planner = None
    if config.coordinator.provider in ("gemini", "anthropic"):
        from google.adk.planners import BuiltInPlanner
        from google.genai import types
        planner = BuiltInPlanner(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True,
                thinking_budget=10240,
            )
        )

    metaops_root = Agent(
        name="metaops_coordinator",
        description=(
            "Enterprise-grade autonomous AI agent with persistent memory, "
            "specialized workflows (vibe_code, full_dev_cycle, deep_research), "
            "shell execution, web research, and code generation pipelines."
        ),
        model=config.coordinator.to_model(),
        static_instruction=_STATIC_PROFILE,
        instruction=dynamic_instruction,
        planner=planner,
        tools=[
            # Execution
            SecureMetaOpsToolset(),
            workstream_tool,
            # Coding workflows
            vibe_coding_tool,
            full_dev_cycle_tool,
            # Research
            deep_research_tool,
            web_search_tool,
            web_extract_tool,
            web_crawl_tool,
            web_map_tool,
            company_info_tool,
            # Reasoning & audit
            thinker_tool,
            audit_tool,
            # Memory, artifacts & skills
            preload_memory,
            load_artifacts,
            request_input,
            memory_search_tool,
            ingest_file_tool,
            skill_executor_tool,
            skill_saver_tool,
            # MCP servers
            *mcp_toolsets,
        ],
        before_agent_callback=auto_inject_memory_callback,
        after_agent_callback=combined_after_agent_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
        on_model_error_callback=on_model_error_callback,
        on_tool_error_callback=on_tool_error_callback,
        code_executor=UnsafeLocalCodeExecutor(),
    )

    # Events compaction: summarize old events to prevent context overflow.
    # Compacts every 20 invocations, keeping 3 invocations of overlap for context.
    compaction_config = EventsCompactionConfig(
        compaction_interval=20,
        overlap_size=3,
    )

    app = App(
        name="metaops_enterprise",
        root_agent=metaops_root,
        events_compaction_config=compaction_config,
    )

    return Runner(
        app=app,
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )


def get_run_config() -> RunConfig:
    """Return RunConfig with safety limits to prevent infinite loops."""
    return RunConfig(
        max_llm_calls=50,
    )
