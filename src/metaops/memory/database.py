import aiosqlite
from pathlib import Path
from typing import List, Optional, Dict, Any

# Resolve DB path via centralized config — single source of truth for .data/ paths.
from metaops.config import MetaOpsConfig
_config = MetaOpsConfig()
DB_PATH = _config.skills_db


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
        """Return a persistent connection, creating it if needed."""
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
        return self._db

    async def initialize(self):
        db = await self._get_db()
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

    # ── Legacy compat ──────────────────────────────────────────────────────

    async def get_skill_procedure(self, skill_name: str) -> Optional[str]:
        """Backward-compatible: return instructions for approved skills."""
        return await self.get_skill_instructions(skill_name)

    async def commit_skill_legacy(self, name: str, trigger: str, procedure: str):
        """Legacy compat: wraps old (name, trigger, procedure) signature."""
        await self.commit_skill(name, description=trigger, instructions=procedure)
