# Plan de correction — Thinking Budget & Gateways

## Contexte
Les modèles OpenAI (o3/o4) et Anthropic (Claude) passent en mode thinking et bloquent la production d'artefacts. Audit gemini.md identifie 4 causes racines, toutes vérifiées dans le code source.

---

## P0 — Bloquant (les modèles ne produisent rien)

### 1. `root.py:167` — Réduire le thinking budget
- `thinking_budget=10240` → `thinking_budget=2048`
- Laisse ~6000 tokens pour l'artefact (limite API Anthropic = 8192)

### 2. `config.py:244` — Brider max_tokens pour Anthropic
- Forcer `max_tokens = min(self.max_tokens, 8192)` avant de passer à `AnthropicLlm`
- Évite les erreurs 400 de l'API

### 3. `config.py:155` — Ajuster le default max_tokens
- `16000` est trop haut pour Anthropic (8192 max)
- Proposer un default par provider : `8192` pour Anthropic, `32000` pour les autres

---

## P1 — Important (artefacts incomplets, écran gelé)

### 4. `telegram.py` et `cli.py` — Ajouter la boucle de continuation
- Porter la logique de `agent_runner.py:83-105` (détection `metaops_truncated` + `_CONTINUE_PROMPT`)
- Les gateways pourront continuer une réponse tronquée au lieu de renvoyer un artefact vide

### 5. Configurer OpenAI via LiteLLM au lieu du driver natif ADK
- Le driver `_openai_llm.py` ignore `reasoning_content` (Cause D)
- Le driver `lite_llm.py` le gère correctement
- Pour les providers OpenAI-compatibles (OpenRouter), forcer le passage par LiteLLM

---

## P2 — Amélioration (expérience utilisateur)

### 6. Injecter `reasoning_effort="low"` pour les modèles o3/o4
- Via LiteLLM, passer `reasoning_effort` dans les kwargs
- Réduit le temps de thinking sans le désactiver complètement

### 7. Filtrage des balises `<think>` dans les gateways (pattern Hermes)
- Détecter et supprimer les blocs `<think>`, `<reasoning>`, `<thinking>`, `<thought>` du texte brut
- State machine pour gérer les tags partiels aux frontières de buffer
- Évite que le thinking apparaisse dans la sortie utilisateur

### 8. Injection mid-turn dans les gateways (pattern Nanobot)
- Quand un message arrive pendant le traitement, le mettre en file d'attente
- Injecter dans la conversation active au prochain tour
- Évite la perte de messages ou le blocage

### 9. Checkpointing de session (pattern Nanobot)
- Persister l'état à chaque tool call dans un fichier JSON
- En cas de crash, reprendre automatiquement
- Basique : 1 fichier par session, overwrite à chaque tour

---

## Fichiers modifiés

| Fichier | Modification | Priorité |
|---|---|---|
| `root.py:167` | `thinking_budget=2048` | P0 |
| `config.py:244` | Bridage `max_tokens` à 8192 pour Anthropic | P0 |
| `config.py:155` | Default `max_tokens` par provider | P0 |
| `config.py:176` | Forcer LiteLLM pour OpenAI (contourner driver natif) | P1 |
| `telegram.py` | Boucle de continuation + filtrage think tags + injection mid-turn | P1-P2 |
| `cli.py` | Boucle de continuation + filtrage think tags | P1-P2 |
| `workflows/agent_runner.py` | Extraire la logique de continuation en module partagé | P1 |
| `core/session_checkpoint.py` | Nouveau : checkpoint basique pour les sessions | P2 |

## Ce qui ne change pas
- `ReasoningGuardedOpenAILlm` reste tel quel (détection de secours)
- Les prompts/instructions de l'agent
- Le `BuiltInPlanner` pour Gemini (fonctionne via driver natif)

---

## Todo optimisée (avec dépendances)

```
T1  [P0] root.py — thinking_budget=2048
    → Aucune dépendance, modif isolée, testable immédiatement

T2  [P0] config.py — Bridage max_tokens Anthropic
    → Aucune dépendance, modif isolée

T3  [P0] config.py — Default max_tokens par provider
    → Dépend de T2 (même fichier, zone proche)

T4  [P1] Extraire logique continuation → module partagé
    → Aucune dépendance, refacteur de agent_runner.py
    → Crée : core/continuation.py

T5  [P1] config.py — Forcer LiteLLM pour OpenAI
    → Aucune dépendance, modif dans _build_openai()
    → Vérifier que LiteLLM est déjà dans les dépendances

T6  [P1] telegram.py — Boucle continuation + filtrage think tags
    → Dépend de T4 (importe le module continuation)
    → Dépend de T5 (pour le filtrage reasoning_content)

T7  [P1] cli.py — Boucle continuation + filtrage think tags
    → Dépend de T4 (même module partagé)

T8  [P2] Injection mid-turn gateways
    → Dépend de T6/T7 (s'ajoute au même niveau)

T9  [P2] core/session_checkpoint.py — Checkpoint basique
    → Aucune dépendance, module indépendant

T10 [P2] config.py — reasoning_effort="low" pour o3/o4
     → Dépend de T5 (via LiteLLM)
```

### Ordre d'exécution recommandé

```
Semaine 1 (P0) :
  T1 → T2 → T3   (3 mods rapides, impact max)

Semaine 1 (P1) :
  T4 → T5         (prérequis pour les gateways)

Semaine 2 (P1) :
  T6 → T7         (gateways avec continuation)

Semaine 2-3 (P2) :
  T8, T9, T10     (améliorations, indépendantes entre elles)
```
