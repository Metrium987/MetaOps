import aiosqlite
import os
from typing import List, Optional

DB_PATH = os.getenv("METAOPS_SKILLS_DB", os.getenv("METAOPS_DB_PATH", "./data/metaops_skills.db"))

class MemoryDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def initialize(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE,
                    trigger_pattern TEXT,
                    procedure TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    async def commit_skill(self, name: str, trigger: str, procedure: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO skills (name, trigger_pattern, procedure) VALUES (?, ?, ?)",
                (name, trigger, procedure)
            )
            await db.commit()

    async def get_skill_procedure(self, skill_name: str) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT procedure FROM skills WHERE name = ?", (skill_name,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def list_skill_names(self) -> List[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT name FROM skills")
            return [row[0] for row in await cursor.fetchall()]
