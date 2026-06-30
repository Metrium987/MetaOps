import logging
import httpx
from typing import Optional
from metaops.gateway.registry import GatewayRegistry

logger = logging.getLogger(__name__)

class DeliveryService:
    def __init__(self, registry: GatewayRegistry, telegram_token: Optional[str] = None):
        self.registry = registry
        self.telegram_token = telegram_token

    async def deliver(self, target: str, message: str) -> bool:
        """
        Route a message to a target:
        - 'cli' -> Prints to local CLI console
        - 'telegram' -> Sends to the home channel via active Telegram gateway
        - 'telegram:<chat_id>' -> Sends to a specific Telegram chat (active or standalone)
        """
        target = target.strip()
        
        if target.lower() == "cli":
            print(f"\n\033[94m[SYSTEM DELIVERY -> CLI]\033[0m\n{message}\n")
            return True

        if target.lower().startswith("telegram"):
            chat_id = None
            if ":" in target:
                chat_id = target.split(":", 1)[1]

            # If the Telegram gateway is active, send via active bot session
            if self.registry.is_active("telegram"):
                telegram_gateway = self.registry.get("telegram")
                if telegram_gateway and hasattr(telegram_gateway, "send_direct_message"):
                    actual_chat_id = chat_id or getattr(telegram_gateway, "default_chat_id", None)
                    if actual_chat_id:
                        try:
                            await telegram_gateway.send_direct_message(actual_chat_id, message)
                            return True
                        except Exception as e:
                            logger.error("Failed to send via active Telegram gateway: %s", e)

            # Fallback to standalone/offline HTTP post if token is available
            if self.telegram_token and chat_id:
                try:
                    async with httpx.AsyncClient() as client:
                        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                        payload = {"chat_id": chat_id, "text": message}
                        res = await client.post(url, json=payload, timeout=10.0)
                        if res.status_code == 200:
                            logger.info("Message sent standalone to Telegram chat %s", chat_id)
                            return True
                        else:
                            logger.error("Standalone Telegram post failed with status %d: %s", res.status_code, res.text)
                except Exception as e:
                    logger.error("Error during standalone Telegram delivery: %s", e)
            else:
                logger.warning("Telegram target specified but no token/chat_id available or gateway inactive")

        # Fallback console printing
        print(f"\n\033[93m[SYSTEM DELIVERY FALLBACK -> {target}]\033[0m\n{message}\n")
        return False
