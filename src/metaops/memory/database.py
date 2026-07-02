import aiosqlite
from pathlib import Path
from typing import List, Optional, Dict, Any

# Resolve DB path via centralized config — single source of truth for .data/ paths.
from metaops.config import get_config
DB_PATH = get_config().skills_db


class MemoryDatabase:
    """Unified skill store: SQLite as source of truth, structured L1/L2/L3.

    L1 (Metadata)  : name, description  — lightweight, for discovery
    L2 (Instructions): instructions      — full procedure, loaded on demand
    L3 (Resources) : skill_resources    — scripts/refs, loaded on demand

    Skills start as ``pending_review`` and must be explicitly approved
    before they become executable.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def _get_db(self) -> aiosqlite.Connection:
        """Return a persistent connection, creating it if needed, and enabling WAL."""
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL;")
            await self._db.execute("PRAGMA foreign_keys=ON;")
        return self._db

    async def initialize(self):
        db = await self._get_db()
        # Skills table (L1/L2 metadata and instructions)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL,
                instructions TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_review',
                version INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Skills resources table (L3 file dependencies)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS skill_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                path TEXT NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY (skill_name) REFERENCES skills(name) ON DELETE CASCADE,
                UNIQUE(skill_name, path)
            )
        """)
        # RAG sources tracking (ingested file dependencies)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rag_sources (
                file_path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                description TEXT,
                global_context TEXT,
                file_size INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Portkey LLM Gateway logs
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portkey_logs (
                id TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                role TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt TEXT,
                completion TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cost REAL,
                latency_ms INTEGER,
                status_code INTEGER,
                error_message TEXT
            )
        """)
        # Subagent execution logs
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subagent_logs (
                id TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                parent_agent TEXT NOT NULL,
                subagent_name TEXT NOT NULL,
                query TEXT,
                response TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                latency_ms INTEGER,
                status TEXT
            )
        """)
        # Loop context storage (query + result + metadata for autonomous loops)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS loop_context (
                id TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                loop_type TEXT NOT NULL,
                query TEXT NOT NULL,
                result TEXT,
                iterations INTEGER DEFAULT 0,
                approved INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                app_name TEXT,
                user_id TEXT
            )
        """)
        # Observability indices
        await db.execute("CREATE INDEX IF NOT EXISTS idx_portkey_logs_session_id ON portkey_logs(session_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_portkey_logs_role ON portkey_logs(role);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_portkey_logs_timestamp ON portkey_logs(timestamp);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subagent_logs_session_id ON subagent_logs(session_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subagent_logs_subagent ON subagent_logs(subagent_name);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_subagent_logs_timestamp ON subagent_logs(timestamp);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_loop_context_loop_type ON loop_context(loop_type);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_loop_context_session_id ON loop_context(session_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_loop_context_timestamp ON loop_context(timestamp);")
        await db.commit()

    # ── Write ──────────────────────────────────────────────────────────────

    async def commit_skill(
        self,
        name: str,
        description: str,
        instructions: str,
        resources: Optional[List[Dict[str, str]]] = None,
        *,
        status: str = "pending_review",
    ):
        """Create or update a skill with explicit L1/L2/L3 fields."""
        db = await self._get_db()
        await db.execute("""
            INSERT INTO skills (name, description, instructions, status, version)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                instructions = excluded.instructions,
                status = excluded.status,
                version = version + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (name, description, instructions, status))

        if resources:
            await db.execute("DELETE FROM skill_resources WHERE skill_name = ?", (name,))
            for res in resources:
                await db.execute(
                    "INSERT INTO skill_resources (skill_name, path, content) VALUES (?, ?, ?)",
                    (name, res["path"], res["content"]),
                )

        await db.commit()

    async def approve_skill(self, skill_name: str) -> bool:
        """Approve a skill — sets status to 'approved'."""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE skills SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE name = ? AND status = 'pending_review'",
            (skill_name,),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def reject_skill(self, skill_name: str) -> bool:
        """Reject a skill — sets status to 'rejected'."""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE skills SET status = 'rejected', updated_at = CURRENT_TIMESTAMP WHERE name = ? AND status = 'pending_review'",
            (skill_name,),
        )
        await db.commit()
        return cursor.rowcount > 0

    # ── Read ───────────────────────────────────────────────────────────────

    async def get_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Fetch full skill data (L1 + L2). Returns None if not found."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT name, description, instructions, status, version, created_at, updated_at FROM skills WHERE name = ?",
            (skill_name,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)

    async def get_skill_instructions(self, skill_name: str) -> Optional[str]:
        """Fetch only L2 instructions. Returns None if not found or not approved."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT instructions FROM skills WHERE name = ? AND status = 'approved'",
            (skill_name,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_skill_resources(self, skill_name: str) -> List[Dict[str, str]]:
        """Fetch L3 resources for a skill."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT path, content FROM skill_resources WHERE skill_name = ?",
            (skill_name,),
        )
        return [{"path": row[0], "content": row[1]} for row in await cursor.fetchall()]

    async def list_skill_names(self, *, status: Optional[str] = None) -> List[str]:
        """List skill names, optionally filtered by status."""
        db = await self._get_db()
        if status:
            cursor = await db.execute("SELECT name FROM skills WHERE status = ?", (status,))
        else:
            cursor = await db.execute("SELECT name FROM skills")
        return [row[0] for row in await cursor.fetchall()]

    async def list_skills_summary(self) -> List[Dict[str, Any]]:
        """List all skills with L1 metadata — used for discovery."""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT name, description, status, version FROM skills ORDER BY updated_at DESC"
        )
        return [dict(row) for row in await cursor.fetchall()]

    # ── Loop Context ──────────────────────────────────────────────────────

    async def save_loop_context(
        self,
        loop_id: str,
        session_id: str,
        loop_type: str,
        query: str,
        result: str,
        iterations: int,
        approved: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: int = 0,
        status: str = "completed",
        app_name: str = "",
        user_id: str = "",
    ):
        """Save autonomous loop execution context to the database."""
        db = await self._get_db()
        await db.execute("""
            INSERT OR REPLACE INTO loop_context (
                id, session_id, loop_type, query, result, iterations, approved,
                prompt_tokens, completion_tokens, total_tokens, latency_ms,
                status, app_name, user_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            loop_id, session_id, loop_type, query, result, iterations,
            1 if approved else 0, prompt_tokens, completion_tokens, total_tokens,
            latency_ms, status, app_name, user_id,
        ))
        await db.commit()

    async def get_recent_loop_context(
        self,
        loop_type: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get recent loop executions of a given type."""
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT id, query, result, iterations, approved, timestamp, status
            FROM loop_context
            WHERE loop_type = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (loop_type, limit))
        return [dict(row) for row in await cursor.fetchall()]

    async def search_loop_context(
        self,
        query: str,
        loop_type: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search loop context by query similarity (simple LIKE match)."""
        db = await self._get_db()
        if loop_type:
            cursor = await db.execute("""
                SELECT id, loop_type, query, result, iterations, approved, timestamp
                FROM loop_context
                WHERE loop_type = ? AND query LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (loop_type, f"%{query}%", limit))
        else:
            cursor = await db.execute("""
                SELECT id, loop_type, query, result, iterations, approved, timestamp
                FROM loop_context
                WHERE query LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (f"%{query}%", limit))
        return [dict(row) for row in await cursor.fetchall()]

    async def get_loop_status(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get status of recent loop executions (running + completed)."""
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT id, loop_type, query, iterations, approved, status, timestamp
            FROM loop_context
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in await cursor.fetchall()]

    async def get_running_loops(self) -> List[Dict[str, Any]]:
        """Get all loops currently running."""
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT id, loop_type, query, iterations, timestamp
            FROM loop_context
            WHERE status = 'running'
            ORDER BY timestamp DESC
        """)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_loop_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics for all loops."""
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT loop_type, COUNT(*) as total,
                   SUM(CASE WHEN approved = 1 THEN 1 ELSE 0 END) as approved_count,
                   AVG(iterations) as avg_iterations,
                   AVG(total_tokens) as avg_tokens
            FROM loop_context
            WHERE status = 'completed'
            GROUP BY loop_type
        """)
        stats = {}
        for row in await cursor.fetchall():
            stats[row[0]] = {
                "total": row[1],
                "approved": row[2],
                "avg_iterations": round(row[3], 1) if row[3] else 0,
                "avg_tokens": round(row[4], 0) if row[4] else 0,
            }
        return stats

    async def get_context_for_agent(
        self,
        loop_type: str,
        query: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Get relevant past contexts for a sub-agent to consult.

        Returns approved results of the same loop type, ordered by relevance.
        Sub-agents can use this as fallback when session.state transmission fails.
        """
        db = await self._get_db()
        cursor = await db.execute("""
            SELECT query, result, iterations, timestamp
            FROM loop_context
            WHERE loop_type = ? AND approved = 1
            ORDER BY timestamp DESC
            LIMIT ?
        """, (loop_type, limit))
        return [dict(row) for row in await cursor.fetchall()]

    # ── Legacy compat ──────────────────────────────────────────────────────

    async def get_skill_procedure(self, skill_name: str) -> Optional[str]:
        """Backward-compatible: return instructions for approved skills."""
        return await self.get_skill_instructions(skill_name)

    async def commit_skill_legacy(self, name: str, trigger: str, procedure: str):
        """Legacy compat: wraps old (name, trigger, procedure) signature."""
        await self.commit_skill(name, description=trigger, instructions=procedure)


# ── Module-level singleton ────────────────────────────────────────────────────

_singleton_db: Optional[MemoryDatabase] = None
_singleton_connection: Optional[aiosqlite.Connection] = None


def get_db_singleton() -> MemoryDatabase:
    """Return a shared MemoryDatabase instance (singleton pattern)."""
    global _singleton_db
    if _singleton_db is None:
        _singleton_db = MemoryDatabase()
    return _singleton_db


async def get_db() -> aiosqlite.Connection:
    """Return a shared database connection, creating it if needed."""
    global _singleton_connection
    singleton = get_db_singleton()
    if _singleton_connection is None:
        _singleton_connection = await singleton._get_db()
    return _singleton_connection


async def initialize_db():
    """Initialize the shared database (create tables if needed)."""
    singleton = get_db_singleton()
    await singleton.initialize()
