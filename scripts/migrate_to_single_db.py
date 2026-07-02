#!/usr/bin/env python3
import os
import sqlite3
import shutil
from pathlib import Path

# central config
from metaops.config import get_config

def migrate():
    config = get_config()
    db_path = Path(config.database_path)
    sessions_db_path = Path(db_path.parent / "metaops_sessions.db")
    skills_db_path = Path(db_path.parent / "metaops_skills.db")

    print(f"Target Consolidated Database: {db_path}")
    print(f"Source Sessions Database: {sessions_db_path}")
    print(f"Source Skills Database: {skills_db_path}\n")

    # If sessions_db_path doesn't exist, try the absolute resolve fallback
    if not sessions_db_path.exists():
        sessions_db_path = db_path.parent / "metaops_sessions.db"
    if not skills_db_path.exists():
        skills_db_path = db_path.parent / "metaops_skills.db"

    # Make sure target directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Connect to target DB
    conn_target = sqlite3.connect(db_path)
    conn_target.row_factory = sqlite3.Row
    cursor_target = conn_target.cursor()

    # Enable WAL
    cursor_target.execute("PRAGMA journal_mode=WAL;")
    cursor_target.execute("PRAGMA foreign_keys=ON;")

    # Initialize all schemas
    # ADK Session Service
    cursor_target.execute("""
        CREATE TABLE IF NOT EXISTS app_states (
            app_name TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            update_time REAL NOT NULL
        )
    """)
    cursor_target.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            app_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            state TEXT NOT NULL,
            update_time REAL NOT NULL,
            PRIMARY KEY (app_name, user_id)
        )
    """)
    cursor_target.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            app_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            id TEXT NOT NULL,
            state TEXT NOT NULL,
            create_time REAL NOT NULL,
            update_time REAL NOT NULL,
            PRIMARY KEY (app_name, user_id, id)
        )
    """)
    cursor_target.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT NOT NULL,
            app_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            invocation_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            event_data TEXT NOT NULL,
            PRIMARY KEY (app_name, user_id, session_id, id),
            FOREIGN KEY (app_name, user_id, session_id) REFERENCES sessions(app_name, user_id, id) ON DELETE CASCADE
        )
    """)
    # Memory Skills
    cursor_target.execute("""
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
    cursor_target.execute("""
        CREATE TABLE IF NOT EXISTS skill_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL,
            path TEXT NOT NULL,
            content TEXT NOT NULL,
            FOREIGN KEY (skill_name) REFERENCES skills(name) ON DELETE CASCADE,
            UNIQUE(skill_name, path)
        )
    """)
    # Portkey LLM Logs
    cursor_target.execute("""
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
    cursor_target.execute("""
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
    # Observability indices
    cursor_target.execute("CREATE INDEX IF NOT EXISTS idx_portkey_logs_session_id ON portkey_logs(session_id);")
    cursor_target.execute("CREATE INDEX IF NOT EXISTS idx_portkey_logs_role ON portkey_logs(role);")
    cursor_target.execute("CREATE INDEX IF NOT EXISTS idx_portkey_logs_timestamp ON portkey_logs(timestamp);")
    cursor_target.execute("CREATE INDEX IF NOT EXISTS idx_subagent_logs_session_id ON subagent_logs(session_id);")
    cursor_target.execute("CREATE INDEX IF NOT EXISTS idx_subagent_logs_subagent ON subagent_logs(subagent_name);")
    cursor_target.execute("CREATE INDEX IF NOT EXISTS idx_subagent_logs_timestamp ON subagent_logs(timestamp);")
    conn_target.commit()

    print("[1/3] Target schema initialized.")

    # 1. Migrate Sessions DB
    if sessions_db_path.exists() and sessions_db_path.resolve() != db_path.resolve():
        print(f"[2/3] Migrating data from {sessions_db_path.name}...")
        conn_src = sqlite3.connect(sessions_db_path)
        conn_src.row_factory = sqlite3.Row
        cursor_src = conn_src.cursor()

        for table in ["app_states", "user_states", "sessions", "events", "portkey_logs", "subagent_logs"]:
            try:
                cursor_src.execute(f"SELECT * FROM {table}")
                rows = cursor_src.fetchall()
                if not rows:
                    continue
                keys = rows[0].keys()
                columns = ", ".join(keys)
                placeholders = ", ".join(["?"] * len(keys))
                
                # Use INSERT OR IGNORE to prevent duplicate errors
                query = f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})"
                
                cursor_target.executemany(query, [tuple(row) for row in rows])
                print(f"  -> Migrated {len(rows)} rows into '{table}'")
            except sqlite3.OperationalError as e:
                # Table might not exist in old db (e.g. portkey_logs/subagent_logs)
                if "no such table" in str(e):
                    continue
                else:
                    raise e
        conn_src.close()
    else:
        print("[2/3] Source Sessions Database not found or matches target path. Skipping.")

    # 2. Migrate Skills DB
    if skills_db_path.exists() and skills_db_path.resolve() != db_path.resolve():
        print(f"[3/3] Migrating data from {skills_db_path.name}...")
        conn_src = sqlite3.connect(skills_db_path)
        conn_src.row_factory = sqlite3.Row
        cursor_src = conn_src.cursor()

        for table in ["skills", "skill_resources"]:
            try:
                cursor_src.execute(f"SELECT * FROM {table}")
                rows = cursor_src.fetchall()
                if not rows:
                    continue
                keys = rows[0].keys()
                columns = ", ".join(keys)
                placeholders = ", ".join(["?"] * len(keys))
                
                query = f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})"
                cursor_target.executemany(query, [tuple(row) for row in rows])
                print(f"  -> Migrated {len(rows)} rows into '{table}'")
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    continue
                else:
                    raise e
        conn_src.close()
    else:
        print("[3/3] Source Skills Database not found or matches target path. Skipping.")

    conn_target.commit()
    conn_target.close()
    print("\n[OK] Migration completed successfully!")

if __name__ == "__main__":
    migrate()
