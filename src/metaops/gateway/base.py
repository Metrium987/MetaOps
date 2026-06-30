from abc import ABC, abstractmethod
from google.adk.events import Event

class PlatformBridge(ABC):
    @abstractmethod
    async def start(self): pass
    @abstractmethod
    async def send_event(self, event: Event): pass
