import os
import shlex
from typing import Optional

FORBIDDEN_COMMAND_PREFIXES = {"rm", "sudo", "mkfs", "format", "dd"}


def check_command_allowed(command: str, user_role: str) -> Optional[str]:
    """Return an error message if `command` should be rejected for `user_role`, else None.

    Defense-in-depth only: a denylist of dangerous binaries by leading token.
    It does not stop chained/sub-interpreted commands (`a; rm -rf .`,
    `bash -c "rm -rf /"`, piping to a shell, etc.) — admins bypass it
    entirely, non-admin roles are blocked only from the listed binaries.
    """
    if user_role == "admin":
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "Command parsing failed. Rejected for security reasons."
    for tok in tokens:
        base_tok = os.path.basename(tok.replace("\\", "/")).lower()
        if any(base_tok.startswith(f) for f in FORBIDDEN_COMMAND_PREFIXES):
            return "Insufficient permissions to execute sensitive commands."
    return None
