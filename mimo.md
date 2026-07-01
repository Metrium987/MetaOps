# Audit & Corrections — MetaOps v3.0.0

## État : Toutes les corrections implémentées

### P0 — Corrigé
| # | Fichier | Correction |
|---|---------|-----------|
| T1 | `bootstrap.py` | Schéma SQLite harmonisé (description, instructions, status, version) + table skill_resources |
| T2 | `config.py` | Env vars sauvées/restaurées dans _build_openai/_build_anthropic |
| T3 | `telegram.py`, `cron.py` | RunConfig(max_llm_calls) ajouté aux runners |

### P1 — Corrigé
| # | Fichier | Correction |
|---|---------|-----------|
| T4 | `session_manager.py`, `telegram.py` | Clé platform:user_id + continuation dans _drain_pending |
| T5 | `callbacks.py`, `telegram.py`, `cli.py` | _SENSITIVE_TOOLS nettoyé + SessionCheckpoint intégré |

### P2 — Corrigé
| # | Fichier | Correction |
|---|---------|-----------|
| T6 | `embeddings.py`, `rag_tools.py`, `main.py`, `dev_cycle.py`, `skill_executor.py`, `README.md`, `.env.example`, `mcp_loader.py` | Cache thread-safe, project root, traduction, détection améliorée, documentation |

### Optimisations architecturales
| # | Fichier | Correction |
|---|---------|-----------|
| T7 | `config.py` + 8 modules | Singleton get_config() — 1 seule instance au lieu de 7 |
| T7 | `main.py` | Cron prompt corrigé (disk usage → security audit) |
| T7 | `vector_service.py` | persona_memory supprimée (inutilisée) |
| T8 | `embeddings.py` | Cache key = hash() au lieu de tuple |
| T8 | `database.py` | Connection SQLite persistante au lieu de 1 par opération |
| T9 | `continuation.py`, `telegram.py`, `cli.py` | run_turn_with_continuation() extrait (~80 lignes de duplication supprimées) |
| T10 | `root.py` | BuiltInPlanner détecte Gemini/Anthropic via model name (pas juste provider) |
| T10 | `agent_runner.py` | Runner cache par agent (pas de recréation à chaque appel) |
| T11 | `vibe_coding.py`, `dev_cycle.py` | tool_context passé au coder pour role-gating complet |
| T11 | `_shell_guard.py` | Détection sub-invocation (bash -c, python -c, etc.) + shutdown/reboot |
| T12 | `session_checkpoint.py` | Auto-cleanup des checkpoints > 24h |
| T12 | `config.py` | Documentation de la décision LocalOpenAILlm vs LiteLLM |

## Tests : 30/30 passent
