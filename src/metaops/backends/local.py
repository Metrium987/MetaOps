import asyncio
import logging
import shutil
import sys
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_BYTES = 256_000
KILL_REAP_TIMEOUT_SECONDS = 5


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

    async def execute_stream(
        self,
        command: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> AsyncGenerator[str, None]:
        """Run `command` in a shell and stream decoded output lines.

        Bounded on two axes so a single tool call can't hang or balloon the
        process's memory: a wall-clock timeout, and a cap on total output
        bytes (a runaway command like `yes` would otherwise accumulate
        unbounded output in the caller before any truncation happens). The
        subprocess is always killed and reaped on any exit path — normal
        completion, timeout, output cap, or the generator being cancelled/
        closed by its caller — to avoid leaking zombie/orphan processes.
        """
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            executable=self.executable,
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        bytes_yielded = 0
        try:
            if process.stdout is not None:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        yield f"\n[execution timed out after {timeout}s — process terminated]\n"
                        break
                    try:
                        line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
                    except asyncio.TimeoutError:
                        yield f"\n[execution timed out after {timeout}s — process terminated]\n"
                        break
                    if not line:
                        break
                    bytes_yielded += len(line)
                    yield line.decode("utf-8", errors="replace")
                    if bytes_yielded >= max_output_bytes:
                        yield f"\n[output truncated at {max_output_bytes} bytes — process terminated]\n"
                        break
        finally:
            if process.returncode is None:
                process.kill()
                # The shell can fork children (e.g. `cmd /c "ping ..."`,
                # backgrounded jobs) that inherit the stdout pipe and outlive
                # the killed shell — process.wait() then blocks on the pipe
                # closing, not on the shell's own exit. Don't let that hang
                # the agent turn; give up after a short grace period.
                try:
                    await asyncio.wait_for(process.wait(), timeout=KILL_REAP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Process %s did not exit within %ss of being killed "
                        "(likely an orphaned child still holding the output pipe)",
                        process.pid, KILL_REAP_TIMEOUT_SECONDS,
                    )
            else:
                await process.wait()
