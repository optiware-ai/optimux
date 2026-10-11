import pytest
from optimux_backend.core.executor import run_shell_command

# cwd is passed explicitly so these tests don't depend on local settings.json.

@pytest.mark.asyncio
async def test_run_shell_command_success(tmp_path):
    result = await run_shell_command("echo 'test output'", cwd=str(tmp_path))
    assert result["exit_code"] == 0
    assert "test output" in result["stdout"]
    assert result["stderr"] == ""

@pytest.mark.asyncio
async def test_run_shell_command_failure(tmp_path):
    result = await run_shell_command("ls /path/to/nonexistent/directory/that/will/fail", cwd=str(tmp_path))
    assert result["exit_code"] != 0
    assert result["stderr"] != ""

@pytest.mark.asyncio
async def test_run_shell_command_timeout(tmp_path):
    result = await run_shell_command("sleep 3", timeout=1, cwd=str(tmp_path))
    assert result["exit_code"] == 124
    assert "Command timed out" in result["stderr"]