from typing import Dict
class SessionManager:
    def __init__(self): self._user_to_session: Dict[str, str] = {}
    def get_session_id(self, platform: str, user_id: str) -> str:
        if user_id not in self._user_to_session:
            self._user_to_session[user_id] = f"metaops_session_{user_id}"
        return self._user_to_session[user_id]
