import asyncio
from typing import AsyncGenerator

class LocalTerminalBackend:
    def __init__(self, executable: str = "/bin/bash"):
        self.executable = executable

    async def execute_stream(self, command: str) -> AsyncGenerator[str, None]:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, executable=self.executable
        )
        if process.stdout is not None:
            async for line in process.stdout:
                yield line.decode('utf-8', errors='replace')
        await process.wait()
