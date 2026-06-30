from typing import Dict, Optional
from metaops.gateway.base import BaseGateway

class GatewayRegistry:
    def __init__(self):
        self._gateways: Dict[str, BaseGateway] = {}
        self._active: Dict[str, bool] = {}

    def register(self, name: str, gateway: BaseGateway):
        self._gateways[name] = gateway

    def set_active(self, name: str, active: bool):
        self._active[name] = active

    def is_active(self, name: str) -> bool:
        return self._active.get(name, False)

    def get(self, name: str) -> Optional[BaseGateway]:
        return self._gateways.get(name)
