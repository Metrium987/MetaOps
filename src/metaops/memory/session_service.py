import sqlite3
import json
import uuid
from pathlib import Path
from typing import Optional
from google.adk.sessions.base_session_service import BaseSessionService, ListSessionsResponse, GetSessionConfig
from google.adk.sessions import Session
from google.adk.sessions.state import State
from google.adk.events import Event

class SQLiteSessionService(BaseSessionService):
    """Persistent session service backed by SQLite. Events are serialized as JSON blobs."""

    def __init__(self, db_path: str = "./metaops_sessions.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    app_name TEXT, user_id TEXT, session_id TEXT,
                    state TEXT, events TEXT,
                    PRIMARY KEY (app_name, user_id, session_id)
                )
            """)
            conn.commit()

    async def create_session(self, *, app_name: str, user_id: str, state: Optional[dict] = None, session_id: Optional[str] = None) -> Session:
        session_id = session_id or str(uuid.uuid4())
        state = state or {}
        with sqlite3.connect(self.db_path) as conn:
            # INSERT OR IGNORE: never overwrite an existing session (would erase history)
            conn.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?, ?, ?, ?, ?)",
                (app_name, user_id, session_id, json.dumps(state), json.dumps([])),
            )
            conn.commit()
        existing = await self.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        return existing or Session(app_name=app_name, user_id=user_id, id=session_id, state=state, events=[])

    async def get_session(self, *, app_name: str, user_id: str, session_id: str, config: Optional[GetSessionConfig] = None) -> Optional[Session]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT state, events FROM sessions WHERE app_name=? AND user_id=? AND session_id=?",
                (app_name, user_id, session_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            state = json.loads(row[0])
            events = [Event.model_validate(e) for e in json.loads(row[1])]
            return Session(app_name=app_name, user_id=user_id, id=session_id, state=state, events=events)

    async def list_sessions(self, *, app_name: str, user_id: Optional[str] = None) -> ListSessionsResponse:
        return ListSessionsResponse(sessions=[])

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM sessions WHERE app_name=? AND user_id=? AND session_id=?",
                (app_name, user_id, session_id),
            )
            conn.commit()

    async def append_event(self, session: Session, event: Event) -> Event:
        event = await super().append_event(session, event)
        if not event.partial:
            # Filter out temp: keys — they are in-memory only and must not be persisted
            persistent_state = {
                k: v for k, v in session.state.items()
                if not k.startswith(State.TEMP_PREFIX)
            }
            with sqlite3.connect(self.db_path) as conn:
                events_data = [e.model_dump(mode='json') for e in session.events]
                conn.execute(
                    "UPDATE sessions SET state=?, events=? WHERE app_name=? AND user_id=? AND session_id=?",
                    (json.dumps(persistent_state), json.dumps(events_data), session.app_name, session.user_id, session.id),
                )
                conn.commit()
        return event
