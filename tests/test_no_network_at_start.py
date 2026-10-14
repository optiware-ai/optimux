"""Starting Optimux makes no connection to the internet.

LiteLLM downloads its model price list from GitHub when it is imported, unless
LITELLM_LOCAL_MODEL_COST_MAP is set first. main.py imported LiteLLM before that
setting, so every start waited for the download (over a minute without a
network). The test imports the server and the admin command in a fresh Python
and records every connection attempt.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
import sys
attempts = []
def hook(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address and address[0] not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(repr(address))
    elif event == "socket.getaddrinfo":
        host = args[0]
        if host not in (None, "localhost", "127.0.0.1", "::1"):
            attempts.append("lookup " + repr(host))
sys.addaudithook(hook)
import importlib
importlib.import_module(sys.argv[1])
print("ATTEMPTS:", attempts)
"""


@pytest.mark.parametrize("module", ["optimux_backend.main", "optimux_backend.admin"])
def test_importing_optimux_connects_to_nothing(tmp_path, module):
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)        # the package must set it itself
    env.update(OPTIMUX_HOME=str(tmp_path / "home"), OPTIMUX_NO_MIGRATE="1")
    r = subprocess.run([sys.executable, "-c", PROBE, module], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "ATTEMPTS: []" in r.stdout, r.stdout[-2000:]
