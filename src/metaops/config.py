import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

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
    "ollama":      (None,                              "OLLAMA_BASE_URL",              "http://localhost:11434"),
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


# Providers natively supported by LiteLLM — no openai/ prefix needed
_LITELLM_NATIVE_PROVIDERS = {
    "openai", "anthropic", "gemini", "groq", "mistral", "cohere",
    "perplexity", "togetherai", "fireworks", "nvidia", "huggingface",
    "xai", "deepseek", "ollama", "azure", "openrouter",
}


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

    def to_litellm(self):
        from google.adk.models import LiteLlm
        model = self.model
        # Custom OpenAI-compatible endpoints need openai/ prefix so LiteLLM
        # routes to the right code path instead of guessing from the model name
        if self.provider not in _LITELLM_NATIVE_PROVIDERS and not model.startswith("openai/"):
            model = f"openai/{model}"
        kwargs = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["api_base"] = self.base_url
        return LiteLlm(model=model, **kwargs)


class MetaOpsConfig:
    def __init__(self):
        self.telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
        self.cron_delivery_target: str = os.getenv("METAOPS_CRON_DELIVERY_TARGET", "cli")
        self.mcp_server_url: str = os.getenv("MCP_SERVER_URL", "http://localhost:8000/sse")

        # Per-agent model configs
        self.coordinator  = ModelConfig("METAOPS_COORDINATOR_MODEL",  "METAOPS_COORDINATOR_PROVIDER",  "openai/gpt-4o")
        self.workstream   = ModelConfig("METAOPS_WORKSTREAM_MODEL",   "METAOPS_WORKSTREAM_PROVIDER",   "openai/gpt-4o-mini")
        self.approver     = ModelConfig("METAOPS_APPROVER_MODEL",     "METAOPS_APPROVER_PROVIDER",     "openai/gpt-4o-mini")
        self.auditor      = ModelConfig("METAOPS_AUDITOR_MODEL",      "METAOPS_AUDITOR_PROVIDER",      "openai/gpt-4o-mini")

        # Database paths
        self.sessions_db: str  = os.getenv("METAOPS_SESSIONS_DB",  "./data/metaops_sessions.db")
        self.skills_db: str    = os.getenv("METAOPS_SKILLS_DB",    "./data/metaops_skills.db")
        self.vector_db: str    = os.getenv("METAOPS_VECTOR_DB",    "./data/metaops_vector_db")

        # Embeddings
        self.embedding_provider: str = os.getenv("METAOPS_EMBEDDING_PROVIDER", "local")
        self.embedding_model: str    = os.getenv("METAOPS_EMBEDDING_MODEL",    "openai/text-embedding-3-small")
        self.embedding_base_url: str = os.getenv("METAOPS_EMBEDDING_BASE_URL", "https://openrouter.ai/api/v1")
        _embed_key = os.getenv("METAOPS_EMBEDDING_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        self.embedding_api_key: Optional[str] = _embed_key

    def validate_keys(self) -> bool:
        if not self.coordinator.api_key:
            provider = os.getenv("METAOPS_COORDINATOR_PROVIDER", "openrouter")
            raise ValueError(
                f"No API key for coordinator (provider: {provider}). "
                "Check OPENROUTER_API_KEY (or the relevant provider key) in .env"
            )
        return True
