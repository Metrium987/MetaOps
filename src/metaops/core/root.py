import os
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.artifacts import FileArtifactService
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.planners import BuiltInPlanner
from google.adk.tools import preload_memory, load_memory
from metaops.memory.session_service import SQLiteSessionService
from metaops.memory.vector_service import HybridVectorMemoryService
from metaops.tools.secure_toolset import SecureMetaOpsToolset
from metaops.tools.escalation import escalation_tool
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
from metaops.core.callbacks import auto_inject_memory_callback, skill_harvest_callback, init_callbacks
from metaops.tools.skill_executor import skill_executor_tool
from metaops.tools.memory_tools import skill_saver_tool, memory_search_tool
from metaops.config import MetaOpsConfig

config = MetaOpsConfig()

session_service = SQLiteSessionService(db_path=config.sessions_db)
memory_service = HybridVectorMemoryService(
    db_path=config.vector_db,
    embedding_provider=config.embedding_provider,
    embedding_model=config.embedding_model,
    embedding_api_key=config.embedding_api_key,
    embedding_base_url=config.embedding_base_url,
)
artifact_service = FileArtifactService(base_dir=os.getenv("METAOPS_ARTIFACTS_DIR", "./data/artifacts"))

init_rag_tools(memory_service)
init_callbacks(memory_service)

_STATIC_PROFILE = """You are MetaOps — an enterprise-grade autonomous agent with a persistent memory system, specialized workflows, and direct access to shell execution, web research, and code generation pipelines.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRICT CONTRACT — Execute all tasks under these absolute rules. Violation = Immediate Halt.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. THINK FIRST
   State your assumptions and your step-by-step plan BEFORE any action or code generation.

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
   Start EVERY response with [STATUS: OK | BLOCKED | PENDING].
   No pleasantries ("I understand", "Sorry", "I'll do my best").
   State raw technical facts only.

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
TOOL SELECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Shell / system tasks          → execute_secure_command
Long bash pipelines           → workstream_executor (isolated, returns 1-sentence summary)
Execute Python directly       → code_executor (built-in, no tool call needed)
Write code (with review)      → vibe_code (coder → reviewer loop, max 3 revisions)
Plan + code + optional tests  → full_dev_cycle (architect → vibe_code → test runner)
Deep web research             → deep_research (parallel gather + structured synthesis)
Quick web lookup              → web_search / web_extract / web_crawl / web_map
Company intelligence          → company_info
Hard decision / tradeoff      → thinker (pass full problem + all context)
Search past conversations     → load_memory (explicit query) — preload_memory runs automatically
Index a file into memory      → ingest_file_dependency
Execute a learned skill       → execute_skill
Code + security audit         → run_audit (bandit + pip-audit + quality scan)
Destructive / irreversible    → request_human_approval FIRST, always
"""
    skills = callback_context.state.get("available_skills", "")
    if skills:
        tool_guide += f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nLEARNED SKILLS AVAILABLE\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n{skills}\n"
    return tool_guide

human_approver_agent = Agent(
    name="human_approver_agent",
    model=config.approver.to_litellm(),
    instruction="You are the approval gateway. Present the requested action clearly to the user and wait for explicit confirmation before proceeding.",
)

def create_runner() -> Runner:
    config.validate_keys()
    mcp_toolsets = load_mcp_toolsets()
    metaops_root = Agent(
        name="metaops_coordinator",
        model=config.coordinator.to_litellm(),
        static_instruction=_STATIC_PROFILE,
        instruction=dynamic_instruction,
        tools=[
            # Execution
            SecureMetaOpsToolset(),
            workstream_tool,
            escalation_tool,
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
            # Memory & skills
            preload_memory,
            load_memory,
            memory_search_tool,
            ingest_file_tool,
            skill_executor_tool,
            skill_saver_tool,
            # MCP servers
            *mcp_toolsets,
        ],
        sub_agents=[human_approver_agent],
        before_agent_callback=auto_inject_memory_callback,
        after_agent_callback=skill_harvest_callback,
        code_executor=UnsafeLocalCodeExecutor(),
        planner=BuiltInPlanner(),
    )
    return Runner(
        agent=metaops_root,
        app_name="metaops_enterprise",
        session_service=session_service,
        memory_service=memory_service,
        artifact_service=artifact_service,
    )
