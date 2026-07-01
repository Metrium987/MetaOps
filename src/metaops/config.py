import logging
import os
import re
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
_cfg_logger = logging.getLogger("metaops.config")

# Sensible default model per provider — used when METAOPS_*_MODEL is not explicitly set
_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openrouter":     "openai/gpt-4o",
    "nousresearch":   "NousResearch/Hermes-3-Llama-3.1-70B",
    "novita":         "meta-llama/llama-3.1-70b-instruct",
    "kilocode":    "anthropic/claude-sonnet-4-6",
    "opencode":    "openai/gpt-4o",
    "openai":      "gpt-4o",
    "anthropic":   "claude-sonnet-4-6-20251001",
    "gemini":      "gemini-2.5-pro",
    "xai":         "grok-3",
    "deepseek":    "deepseek-chat",
    "mistral":     "mistral-large-latest",
    "groq":        "llama-3.3-70b-versatile",
    "perplexity":  "sonar-pro",
    "cohere":      "command-r-plus",
    "togetherai":  "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "fireworks":   "accounts/fireworks/models/llama-v3p3-70b-instruct",
    "nvidia":      "meta/llama-3.3-70b-instruct",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "copilot":     "gpt-4o",
    "arcee":       "arcee-ai/arcee-spotlight",
    "gmi":         "meta-llama/llama-3.3-70b-instruct",
    "azure":       "gpt-4o",
    "alibaba":     "qwen-max",
    "kimi":        "moonshot-v1-8k",
    "minimax":     "MiniMax-Text-01",
    "stepfun":     "step-2-16k",
    "zai":         "glm-4-plus",
    "xiaomi":      "MiMo-7B-RL",
    "tencent":     "hunyuan-turbo",
    "ollama":      "ollama/llama3.2",
    "lmstudio":    "lmstudio/local-model",
}

# Provider registry: name -> (api_key_env | tuple[str,...], base_url_env, default_base_url)
_PROVIDER_DEFAULTS = {
    # Aggregators
    "openrouter":     ("OPENROUTER_API_KEY",            "OPENROUTER_BASE_URL",          "https://openrouter.ai/api/v1"),
    "nousresearch":   ("NOUSRESEARCH_API_KEY",          "NOUSRESEARCH_BASE_URL",        "https://inference-api.nousresearch.com/v1"),
    "novita":         ("NOVITA_API_KEY",                "NOVITA_BASE_URL",              "https://api.novita.ai/v1"),
    "kilocode":    ("KILOCODE_API_KEY",                "KILOCODE_BASE_URL",            "https://api.kilo.ai/api/gateway"),
    "opencode":    ("OPENCODE_ZEN_API_KEY",            "OPENCODE_ZEN_BASE_URL",        "https://opencode.ai/zen/v1"),
    # Cloud providers
    "openai":      ("OPENAI_API_KEY",                  "OPENAI_BASE_URL",              "https://api.openai.com/v1"),
    "anthropic":   ("ANTHROPIC_API_KEY",               "ANTHROPIC_BASE_URL",           "https://api.anthropic.com"),
    "gemini":      (("GOOGLE_API_KEY", "GEMINI_API_KEY"), "GEMINI_BASE_URL",           "https://generativelanguage.googleapis.com/v1beta"),
    "xai":         ("XAI_API_KEY",                     "XAI_BASE_URL",                "https://api.x.ai/v1"),
    "deepseek":    ("DEEPSEEK_API_KEY",                "DEEPSEEK_BASE_URL",            "https://api.deepseek.com/v1"),
    "mistral":     ("MISTRAL_API_KEY",                 "MISTRAL_BASE_URL",             "https://api.mistral.ai/v1"),
    "groq":        ("GROQ_API_KEY",                    "GROQ_BASE_URL",                "https://api.groq.com/openai/v1"),
    "perplexity":  ("PERPLEXITY_API_KEY",              "PERPLEXITY_BASE_URL",          "https://api.perplexity.ai"),
    "cohere":      ("COHERE_API_KEY",                  "COHERE_BASE_URL",              "https://api.cohere.ai/v1"),
    "togetherai":  ("TOGETHER_API_KEY",                "TOGETHER_BASE_URL",            "https://api.together.ai/v1"),
    "fireworks":   ("FIREWORKS_API_KEY",               "FIREWORKS_BASE_URL",           "https://api.fireworks.ai/inference/v1"),
    "nvidia":      ("NVIDIA_API_KEY",                  "NVIDIA_BASE_URL",              "https://integrate.api.nvidia.com/v1"),
    "huggingface": ("HF_TOKEN",                        "HF_BASE_URL",                  "https://router.huggingface.co/v1"),
    "copilot":     (("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
                                                        "COPILOT_API_BASE_URL",         "https://api.githubcopilot.com"),
    "arcee":       ("ARCEEAI_API_KEY",                 "ARCEE_BASE_URL",               "https://api.arcee.ai/api/v1"),
    "gmi":         ("GMI_API_KEY",                     "GMI_BASE_URL",                 "https://api.gmi-serving.com/v1"),
    "azure":       ("AZURE_FOUNDRY_API_KEY",           "AZURE_FOUNDRY_BASE_URL",       ""),
    # Asian providers
    "alibaba":     ("DASHSCOPE_API_KEY",               "DASHSCOPE_BASE_URL",           "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    "kimi":        (("KIMI_API_KEY", "KIMI_CODING_API_KEY"), "KIMI_BASE_URL",          "https://api.moonshot.ai/v1"),
    "minimax":     ("MINIMAX_API_KEY",                 "MINIMAX_BASE_URL",             "https://api.minimax.io/anthropic"),
    "stepfun":     ("STEPFUN_API_KEY",                 "STEPFUN_BASE_URL",             "https://api.stepfun.ai/step_plan/v1"),
    "zai":         (("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), "GLM_BASE_URL",   "https://api.z.ai/api/paas/v4"),
    "xiaomi":      ("XIAOMI_API_KEY",                  "XIAOMI_BASE_URL",              "https://api.xiaomimimo.com/v1"),
    "tencent":     ("TOKENHUB_API_KEY",                "TOKENHUB_BASE_URL",            "https://tokenhub.tencentmaas.com/v1"),
    # Local / self-hosted
    "ollama":      (None,                              "OLLAMA_BASE_URL",              "http://localhost:11434/v1"),
    "lmstudio":    ("LM_API_KEY",                      "LM_BASE_URL",                  "http://127.0.0.1:1234/v1"),
}


def resolve_provider(provider: str) -> tuple[Optional[str], Optional[str]]:
    entry = _PROVIDER_DEFAULTS.get(provider.lower())
    if not entry:
        return None, None
    key_env, url_env, url_default = entry
    if isinstance(key_env, (list, tuple)):
        api_key = next((os.getenv(k) for k in key_env if os.getenv(k)), None)
    else:
        api_key = os.getenv(key_env) if key_env else None
    base_url = (os.getenv(url_env) if url_env else None) or url_default or None
    return api_key, base_url


# Providers that use the native Anthropic Messages API
_ANTHROPIC_NATIVE_PROVIDERS: set[str] = {"anthropic", "minimax"}

# Providers that use the native Google GenAI API
_GEMINI_NATIVE_PROVIDERS: set[str] = {"gemini"}

# Providers whose API is OpenAI-compatible (/v1/chat/completions)
# → all route through LiteLLM with openai/ prefix (see _build_litellm)
_OPENAI_COMPATIBLE_PROVIDERS: set[str] = {
    "openai", "groq", "xai", "deepseek", "fireworks", "nvidia",
    "togetherai", "perplexity", "huggingface", "mistral",
    # Aggregators / inference services
    "openrouter", "nousresearch", "novita", "kilocode", "opencode",
    "arcee", "gmi", "copilot",
    # Asian providers
    "alibaba", "kimi", "stepfun", "xiaomi", "tencent",
    # Local / self-hosted
    "ollama", "lmstudio",
}

# Local / self-hosted providers — get a hardened driver that tolerates
# missing tool_call id/name (common with small local models)
_LOCAL_PROVIDERS: set[str] = {"ollama", "lmstudio"}


def _is_reasoning_model(model_name: str) -> bool:
    """Check if a model is an OpenAI reasoning model (o3, o4, etc.)."""
    pattern = r"^(o3|o4|gpt-5)(?:$|[-.])"
    return bool(re.match(pattern, model_name))


class ModelConfig:
    def __init__(self, model_env: str, provider_env: str, default_model: str, default_provider: str = "openrouter"):
        self.provider = os.getenv(provider_env, default_provider).lower()
        self.api_key, self.base_url = resolve_provider(self.provider)
        explicit_model = os.getenv(model_env)
        if explicit_model:
            self.model = explicit_model
        else:
            # Use provider-specific default if the caller's default_model was built for a different provider
            self.model = _PROVIDER_DEFAULT_MODELS.get(self.provider, default_model)

        # ADK's OpenAILlm defaults max_tokens to 4096 — too small for a tool
        # call whose argument is a full file (e.g. a single-file HTML/CSS/JS
        # game): the completion gets truncated mid-JSON, the arguments fail
        # to parse, and the tool gets called with empty args. Default to a
        # roomier budget; override per-agent via METAOPS_<AGENT>_MAX_TOKENS
        # (derived from model_env, e.g. METAOPS_COORDINATOR_MODEL ->
        # METAOPS_COORDINATOR_MAX_TOKENS).
        max_tokens_env = model_env.rsplit("_MODEL", 1)[0] + "_MAX_TOKENS"
        # Provider-specific defaults: Anthropic API hard-limits at 8192,
        # other providers tolerate larger budgets.
        default_max = "8192" if self.provider in _ANTHROPIC_NATIVE_PROVIDERS else "16000"
        self.max_tokens = int(os.getenv(max_tokens_env, default_max))

    # ── Native driver routing (fastest → slowest) ──────────────────────
    def to_model(self):
        """Return the best ADK model driver for this provider.

        Priority:
          1. Native Anthropic SDK  (anthropic provider → AnthropicLlm)
          2. Native Google GenAI   (gemini provider → Gemini)
          3. LiteLLM              (all OpenAI-compatible endpoints → LiteLlm)
             Rationale: the native OpenAI driver (labs/openai) ignores
             delta.reasoning_content in streaming, causing visual freezes
             during thinking. LiteLLM extracts reasoning_content properly.
          4. LiteLLM fallback      (everything else)
        """
        # ── 1. Anthropic native ──
        if self.provider in _ANTHROPIC_NATIVE_PROVIDERS:
            return self._build_anthropic()

        # ── 2. Gemini native ──
        if self.provider in _GEMINI_NATIVE_PROVIDERS:
            return self._build_gemini()

        # ── 3. LiteLLM for all OpenAI-compatible providers ──
        # The native OpenAI driver (labs/openai/_openai_llm.py) ignores
        # delta.reasoning_content during streaming, causing visual freezes
        # when reasoning models (o3, o4) are used. LiteLLM extracts
        # reasoning_content properly and passes it through.
        #
        # NOTE: LocalOpenAILlm (local_llm_driver.py) patches missing
        # tool_call.id/name for Ollama/LM Studio, but LiteLLM also handles
        # this internally.  We route through LiteLLM for consistency and
        # reasoning_content support.  _build_openai() is kept as a fallback
        # for environments where LiteLLM is unavailable.
        _cfg_logger.debug("Using LiteLLM for provider=%s (reasoning_content support)", self.provider)
        return self._build_litellm()

    def _build_openai(self):
        """Use the native OpenAI SDK driver — no LiteLLM overhead.

        NOTE: Currently unused — to_model() routes all OpenAI-compatible
        providers through _build_litellm() instead, because LiteLLM handles
        reasoning_content properly during streaming. Kept for reference and
        potential future use with local backends.
        """
        from metaops.core.reasoning_guard import ReasoningGuardedOpenAILlm

        model = self.model
        if self.provider == "openai" and model.startswith("openai/"):
            model = model[len("openai/"):]
        elif self.provider in _LOCAL_PROVIDERS:
            for prefix in ("ollama/", "lmstudio/"):
                if model.startswith(prefix):
                    model = model[len(prefix):]
                    break

        # Save and restore env vars to avoid polluting global state when
        # multiple providers coexist in the same process.
        saved = {}
        for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            saved[var] = os.environ.get(var)
        try:
            if self.api_key:
                os.environ["OPENAI_API_KEY"] = self.api_key
            if self.base_url:
                os.environ["OPENAI_BASE_URL"] = self.base_url

            if self.provider in _LOCAL_PROVIDERS:
                from metaops.core.local_llm_driver import LocalOpenAILlm
                _cfg_logger.info(
                    "Hardened local OpenAI driver: provider=%s model=%s max_tokens=%d",
                    self.provider, model, self.max_tokens,
                )
                return LocalOpenAILlm(model=model, max_tokens=self.max_tokens)

            _cfg_logger.info(
                "Native OpenAI driver: provider=%s model=%s max_tokens=%d",
                self.provider, model, self.max_tokens,
            )
            return ReasoningGuardedOpenAILlm(model=model, max_tokens=self.max_tokens)
        finally:
            for var, val in saved.items():
                if val is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = val

    def _build_anthropic(self):
        """Use the native Anthropic SDK driver — direct Messages API."""
        from google.adk.models.anthropic_llm import AnthropicLlm

        model = self.model
        if model.startswith("anthropic/"):
            model = model[len("anthropic/"):]

        # Save and restore env vars to avoid polluting global state when
        # multiple providers coexist in the same process.
        saved = {}
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            saved[var] = os.environ.get(var)
        try:
            if self.api_key:
                os.environ["ANTHROPIC_API_KEY"] = self.api_key
            if self.base_url:
                os.environ["ANTHROPIC_BASE_URL"] = self.base_url

            capped_tokens = min(self.max_tokens, 8192)
            _cfg_logger.info(
                "Native Anthropic driver: provider=%s model=%s max_tokens=%d (capped from %d)",
                self.provider, model, capped_tokens, self.max_tokens,
            )
            return AnthropicLlm(model=model, max_tokens=capped_tokens)
        finally:
            for var, val in saved.items():
                if val is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = val

    def _build_gemini(self):
        """Use the native Google GenAI driver — direct Gemini API."""
        from google.adk.models import Gemini
        from google.genai import types
        model = self.model
        retry_opts = types.HttpRetryOptions(
            attempts=3,
            exp_base=2,
            initial_delay=1,
            http_status_codes=[429, 499, 500, 503, 504],
        )
        _cfg_logger.info("Native Gemini driver: model=%s (retry: 3 attempts)", model)
        return Gemini(model=model, retry_options=retry_opts)

    def _build_litellm(self):
        """Build LiteLLM driver for all OpenAI-compatible providers.

        Uses LiteLLM instead of the native OpenAI driver because the latter
        ignores delta.reasoning_content in streaming, causing visual freezes
        when reasoning models (o3, o4) are used.  For o3/o4 models, injects
        reasoning_effort=low to reduce thinking budget.
        """
        from google.adk.models import LiteLlm
        model = self.model
        # For OpenAI-compatible providers (KiloCode, OpenCode, OpenRouter, etc.),
        # route through openai/ prefix so LiteLLM sends to the correct base_url.
        # The model name after openai/ is passed AS-IS to the API — never strip
        # vendor prefixes like nvidia/, stepfun/, etc.
        if self.provider in _OPENAI_COMPATIBLE_PROVIDERS and not model.startswith("openai/"):
            model = f"openai/{model}"
        kwargs = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url
        # For OpenAI reasoning models (o3, o4), limit reasoning effort to
        # prevent the model from spending the entire output budget on thinking.
        model_name = model.split("/")[-1]
        if _is_reasoning_model(model_name):
            kwargs["reasoning_effort"] = "low"
            _cfg_logger.info("LiteLLM: reasoning_effort=low for model=%s", model)
        return LiteLlm(model=model, **kwargs)


class MetaOpsConfig:
    def __init__(self):
        self.telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
        self.cron_delivery_target: str = os.getenv("METAOPS_CRON_DELIVERY_TARGET", "cli")
        self.mcp_server_url: str = os.getenv("MCP_SERVER_URL", "http://localhost:8000/sse")

        # Comma-separated Telegram numeric user IDs allowed to use the bot.
        _allowed = os.getenv("METAOPS_TELEGRAM_ALLOWED_USERS", "")
        self.telegram_allowed_user_ids: Optional[set[str]] = (
            {u.strip() for u in _allowed.split(",") if u.strip()} or None
        )

        # Default role configs
        self.default_cli_role: str = os.getenv("METAOPS_DEFAULT_CLI_ROLE", "admin")
        self.default_cron_role: str = os.getenv("METAOPS_DEFAULT_CRON_ROLE", "user")
        self.default_telegram_role: str = os.getenv("METAOPS_DEFAULT_TELEGRAM_ROLE", "admin")

        # Per-agent model configs
        self.coordinator  = ModelConfig("METAOPS_COORDINATOR_MODEL",  "METAOPS_COORDINATOR_PROVIDER",  "openai/gpt-4o")
        self.workstream   = ModelConfig("METAOPS_WORKSTREAM_MODEL",   "METAOPS_WORKSTREAM_PROVIDER",   "openai/gpt-4o-mini")
        self.auditor      = ModelConfig("METAOPS_AUDITOR_MODEL",      "METAOPS_AUDITOR_PROVIDER",      "openai/gpt-4o-mini")

        # Database paths
        _project_root = Path(__file__).resolve().parent.parent.parent
        _data = _project_root / ".data"

        def _resolve_db_path(env_key: str, default_name: str) -> str:
            raw = os.getenv(env_key, str(_data / default_name))
            p = Path(raw)
            if not p.is_absolute():
                p = _project_root / p
            return str(p)

        self.sessions_db: str  = _resolve_db_path("METAOPS_SESSIONS_DB",  "metaops_sessions.db")
        self.skills_db: str    = _resolve_db_path("METAOPS_SKILLS_DB",    "metaops_skills.db")
        self.vector_db: str    = _resolve_db_path("METAOPS_VECTOR_DB",    "metaops_vector_db")

        # Embeddings
        self.embedding_provider: str = os.getenv("METAOPS_EMBEDDING_PROVIDER", "local")
        self.embedding_model: str    = os.getenv("METAOPS_EMBEDDING_MODEL",    "openai/text-embedding-3-small")
        self.embedding_base_url: str = os.getenv("METAOPS_EMBEDDING_BASE_URL", "https://openrouter.ai/api/v1")
        _embed_key = os.getenv("METAOPS_EMBEDDING_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        self.embedding_api_key: Optional[str] = _embed_key

        # ── Runtime tunables (all overridable via .env) ──────────────────

        # BuiltInPlanner thinking budget for Gemini/Anthropic (tokens)
        self.thinking_budget: int = int(os.getenv("METAOPS_THINKING_BUDGET", "2048"))

        # Context compaction
        self.compact_interval: int = int(os.getenv("METAOPS_COMPACT_INTERVAL", "20"))
        self.compact_overlap: int  = int(os.getenv("METAOPS_COMPACT_OVERLAP", "3"))

        # Continuation / revision limits
        self.max_continuations: int = int(os.getenv("METAOPS_MAX_CONTINUATIONS", "3"))
        self.max_revisions: int = int(os.getenv("METAOPS_MAX_REVISIONS", "3"))
        self.max_refinement_iterations: int = int(os.getenv("METAOPS_MAX_REFINEMENT_ITERATIONS", "3"))

        # LLM call safety limits
        self.gateway_max_llm_calls: int = int(os.getenv("METAOPS_GATEWAY_MAX_LLM_CALLS", "50"))
        self.cron_max_llm_calls: int    = int(os.getenv("METAOPS_CRON_MAX_LLM_CALLS", "30"))

        # Telegram queue
        self.max_pending_messages: int = int(os.getenv("METAOPS_MAX_PENDING_MESSAGES", "5"))

        # Checkpoint TTL (hours)
        self.checkpoint_ttl_hours: int = int(os.getenv("METAOPS_CHECKPOINT_TTL_HOURS", "24"))

        # Shell execution
        self.shell_timeout: int = int(os.getenv("METAOPS_SHELL_TIMEOUT", "120"))
        self.shell_max_output_bytes: int = int(os.getenv("METAOPS_SHELL_MAX_OUTPUT_BYTES", "256000"))
        self.shell_kill_timeout: int = int(os.getenv("METAOPS_SHELL_KILL_TIMEOUT", "5"))

        # Tool output limits
        self.tool_output_max_chars: int = int(os.getenv("METAOPS_TOOL_OUTPUT_MAX_CHARS", "2000"))

        # RAG chunking
        self.rag_chunk_size: int = int(os.getenv("METAOPS_RAG_CHUNK_SIZE", "1000"))

        # Skill summary display
        self.skill_summary_max_chars: int = int(os.getenv("METAOPS_SKILL_SUMMARY_MAX_CHARS", "500"))

        # Audit limits
        self.audit_max_files: int = int(os.getenv("METAOPS_AUDIT_MAX_FILES", "150"))
        self.audit_file_max_lines: int = int(os.getenv("METAOPS_AUDIT_FILE_MAX_LINES", "8000"))

    def validate_keys(self) -> bool:
        if not self.coordinator.api_key:
            provider = os.getenv("METAOPS_COORDINATOR_PROVIDER", "openrouter")
            raise ValueError(
                f"No API key for coordinator (provider: {provider}). "
                "Check OPENROUTER_API_KEY (or the relevant provider key) in .env"
            )
        return True


# Module-level singleton — all modules share one config instance.
# Avoids 7+ redundant .env parses and provider resolutions at import time.
_config_instance: MetaOpsConfig | None = None


def get_config() -> MetaOpsConfig:
    global _config_instance
    if _config_instance is None:
        _config_instance = MetaOpsConfig()
    return _config_instance
