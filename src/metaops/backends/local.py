import asyncio
import shutil
import sys
from typing import AsyncGenerator


def _detect_shell() -> str | None:
    """Return the best available shell executable for the current platform."""
    if sys.platform == "win32":
        for candidate in ("bash", "powershell", "cmd"):
            path = shutil.which(candidate)
            if path:
                return path
        return None
    return shutil.which("bash") or shutil.which("sh")


class LocalTerminalBackend:
    def __init__(self, executable: str | None = None):
        self.executable = executable or _detect_shell()

    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            executable=self.executable,
        )
        if process.stdout is not None:
            async for line in process.stdout:
                yield line.decode("utf-8", errors="replace")
        await process.wait()
