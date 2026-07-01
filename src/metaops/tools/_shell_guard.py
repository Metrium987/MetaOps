import os
import shlex
from typing import Optional

FORBIDDEN_COMMAND_PREFIXES = {"rm", "sudo", "mkfs", "format", "dd", "shutdown", "reboot", "halt", "poweroff"}

# Commands that can be used to bypass the denylist via sub-invocation
_SUBINVOCATION_COMMANDS = {"bash", "sh", "zsh", "python", "python3", "perl", "ruby", "node", "env", "nohup", "xargs"}


def check_command_allowed(command: str, user_role: str) -> Optional[str]:
    """Return an error message if `command` should be rejected for `user_role`, else None.

    Defense-in-depth only: checks leading tokens and common bypass patterns.
    It does not stop all possible chained/sub-interpreted commands — admins
    bypass it entirely, non-admin roles are blocked from listed binaries and
    obvious sub-invocation patterns.
    """
    if user_role == "admin":
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "Command parsing failed. Rejected for security reasons."

    if not tokens:
        return None

    first_token = os.path.basename(tokens[0].replace("\\", "/")).lower()

    # Direct match on the leading command
    if any(first_token.startswith(f) for f in FORBIDDEN_COMMAND_PREFIXES):
        return "Insufficient permissions to execute sensitive commands."

    # Sub-invocation: "bash -c 'rm -rf /'" or "python -c 'import os; os.system(...)'"
    if first_token in _SUBINVOCATION_COMMANDS and len(tokens) > 1:
        # Join remaining tokens to detect forbidden commands in the sub-command
        rest = " ".join(tokens[1:])
        rest_lower = rest.lower()
        for forbidden in FORBIDDEN_COMMAND_PREFIXES:
            if forbidden in rest_lower:
                return "Insufficient permissions to execute sensitive commands via sub-invocation."

    return None
