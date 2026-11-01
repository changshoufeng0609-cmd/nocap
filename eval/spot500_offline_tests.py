"""Run repository pytest without network or dotenv-file reads.

The one existing test deliberately reading a temporary .env is deselected to
honor the task's literal ban on reading any .env file. No repository file changes.
"""
import os
from pathlib import Path
import sys

sys.path = [p for p in sys.path if Path(p).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(Path.cwd()))


def offline_audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                 "socket.gethostbyname", "socket.gethostbyaddr"}:
        raise RuntimeError("Offline pytest guard refused network access")
    if event == "open" and isinstance(args[0], (str, bytes)):
        name = Path(os.fsdecode(args[0])).name
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            raise RuntimeError("Offline pytest guard refused dotenv file access")


sys.addaudithook(offline_audit)
# Other tests inspect explicit mocked load_dotenv calls. The real loader need
# never inspect a file in this task, even when a code path invokes it incidentally.
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.dotenv_values = lambda *args, **kwargs: {}

import pytest

args = sys.argv[1:] or ["-q", "tests/"]
args += ["--deselect=tests/test_extract_backends.py::test_dotenv_can_select_the_backend_but_the_shell_wins"]
if __name__ == "__main__":
    raise SystemExit(pytest.main(args))
