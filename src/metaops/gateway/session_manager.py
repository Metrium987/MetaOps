from typing import Dict, Set

class SessionManager:
    def __init__(self):
        self._user_to_session: Dict[str, str] = {}
        self._busy_sessions: Set[str] = set()

    def get_session_id(self, platform: str, user_id: str) -> str:
        key = f"{platform}:{user_id}"
        if key not in self._user_to_session:
            self._user_to_session[key] = f"metaops_session_{user_id}"
        return self._user_to_session[key]

    def is_busy(self, session_id: str) -> bool:
        return session_id in self._busy_sessions

    def set_busy(self, session_id: str, busy: bool):
        if busy:
            self._busy_sessions.add(session_id)
        else:
            self._busy_sessions.discard(session_id)

    def clear_session(self, platform: str, user_id: str) -> None:
        key = f"{platform}:{user_id}"
        self._user_to_session.pop(key, None)

