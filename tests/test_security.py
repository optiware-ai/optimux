import os

import pytest

import optimux_backend.core.executor as executor
from optimux_backend.core.executor import run_shell_command
from optimux_backend.core.security import (
    ScopeError, ValidationError, bearer_from_header, check_token, get_or_create_token,
    origin_allowed, paths_outside_scope, resolve_project_dir, resolve_scope_root, scrubbed_env,
    validate_env_key, validate_env_value, validate_project_name,
)


# ---------- auth
def test_check_token():
    assert check_token("abc", "abc")
    assert not check_token("abc", "abd")
    assert not check_token("", "abc")
    assert not check_token("abc", "")


def test_bearer_from_header():
    assert bearer_from_header("Bearer xyz") == "xyz"
    assert bearer_from_header("bearer  xyz ") == "xyz"
    assert bearer_from_header("Basic xyz") == ""
    assert bearer_from_header(None) == ""


def test_get_or_create_token_persists(mocker, tmp_path):
    mocker.patch.dict(os.environ, {}, clear=True)
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=x")  # no trailing newline
    token = get_or_create_token(str(env_file))
    assert len(token) > 30
    assert env_file.read_text() == f"GEMINI_API_KEY=x\nOPTIMUX_TOKEN={token}\n"
    assert get_or_create_token(str(env_file)) == token  # reused, not regenerated


@pytest.mark.parametrize("content,expected", [
    ("OPTIMUX_TOKEN=first\nOPTIMUX_TOKEN=last\n", "last"),     # the one dotenv (the server) uses
    ("CODEMUX_TOKEN=old\n", "old"),                            # a secrets.env from before the rename
    ("CODEMUX_TOKEN=old\nOPTIMUX_TOKEN=new\n", "new"),
])
def test_a_process_without_the_environment_finds_the_saved_token(mocker, tmp_path, content, expected):
    """The admin command doesn't load secrets.env: `optimux token` used to add a second
    token the running server didn't know, and printed that one."""
    mocker.patch.dict(os.environ, {}, clear=True)
    env_file = tmp_path / "secrets.env"
    env_file.write_text(content)
    assert get_or_create_token(str(env_file)) == expected
    assert env_file.read_text() == content          # nothing added


# ---------- origin
@pytest.mark.parametrize("origin,host,extra,ok", [
    (None, "localhost:8000", "", True),
    ("http://localhost:8000", "localhost:8000", "", True),
    ("https://optimux.example.com", "optimux.example.com", "", True),
    ("https://evil.example", "localhost:8000", "", False),
    ("https://evil.example", "localhost:8000", "https://evil.example/", True),
])
def test_origin_allowed(origin, host, extra, ok):
    assert origin_allowed(origin, host, extra) is ok


# ---------- scope
@pytest.mark.parametrize("bad", ["", "..", ".", "../etc", "a/b", ".hidden", "a\\b", "x\x00"])
def test_validate_project_name_rejects(bad):
    with pytest.raises(ScopeError):
        validate_project_name(bad)


def test_resolve_project_dir(tmp_path):
    (tmp_path / "proj").mkdir()
    assert resolve_project_dir(str(tmp_path), "proj") == (tmp_path / "proj").resolve()
    with pytest.raises(ScopeError):
        resolve_project_dir(str(tmp_path), "missing")
    with pytest.raises(ScopeError):
        resolve_project_dir("relative/path", "proj")


def test_symlink_escaping_scope_is_rejected(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (root / "sneaky").symlink_to(outside)
    with pytest.raises(ScopeError):
        resolve_project_dir(str(root), "sneaky")


def test_resolve_scope_root_requires_value():
    with pytest.raises(ScopeError):
        resolve_scope_root("")


# ---------- env keys
@pytest.mark.parametrize("good", ["GEMINI_API_KEY", "GROQ_KEY", "GH_TOKEN", "OLLAMA_API_BASE", "TEST_KEY"])
def test_validate_env_key_accepts(good):
    assert validate_env_key(good) == good


@pytest.mark.parametrize("bad", ["PATH", "LD_PRELOAD", "BASH_ENV", "lower_key", "A=B_KEY", "X\nY_KEY", "OPTIMUX_TOKEN", ""])
def test_validate_env_key_rejects(bad):
    with pytest.raises(ValidationError):
        validate_env_key(bad)


def test_validate_env_value_rejects_newlines():
    with pytest.raises(ValidationError):
        validate_env_value("sk-123\nLD_PRELOAD=/tmp/x.so")


def test_scrubbed_env():
    env = scrubbed_env({"GROQ_KEY"}, base={
        "PATH": "/bin", "GEMINI_API_KEY": "s", "GROQ_KEY": "s", "OPTIMUX_TOKEN": "t", "GH_TOKEN": "g",
    })
    assert env == {"PATH": "/bin", "GH_TOKEN": "g"}


# ---------- executor
@pytest.mark.asyncio
async def test_executor_refuses_without_scope(mocker):
    mocker.patch.object(executor, "get_settings", return_value={"projects_root": "", "active_project": ""})
    result = await run_shell_command("echo hi")
    assert result["exit_code"] == 126
    assert "Scope error" in result["stderr"]


@pytest.mark.asyncio
async def test_executor_refuses_traversal(mocker, tmp_path):
    mocker.patch.object(executor, "get_settings",
                        return_value={"projects_root": str(tmp_path), "active_project": "../"})
    result = await run_shell_command("pwd")
    assert result["exit_code"] == 126


@pytest.mark.asyncio
async def test_executor_runs_in_active_project(mocker, tmp_path):
    (tmp_path / "proj").mkdir()
    mocker.patch.object(executor, "get_settings",
                        return_value={"projects_root": str(tmp_path), "active_project": "proj"})
    result = await run_shell_command("pwd")
    assert result["stdout"].strip() == str((tmp_path / "proj").resolve())


@pytest.mark.asyncio
async def test_executor_hides_provider_keys(mocker, tmp_path):
    # Distinctive values: short ones like "tok" also match ordinary environment
    # text (e.g. a PATH containing "tokenizers") and fail on real machines.
    key, token = "gemini-key-5f3a9c-not-real", "optimux-token-8d21e7-not-real"
    mocker.patch.dict(os.environ, {"GEMINI_API_KEY": key, "OPTIMUX_TOKEN": token})
    result = await run_shell_command("env", cwd=str(tmp_path))
    assert key not in result["stdout"]
    assert token not in result["stdout"]
    assert "GEMINI_API_KEY=" not in result["stdout"] and "OPTIMUX_TOKEN=" not in result["stdout"]


# ---------- outside-project check: heredocs and cd

ROOT = "/home/u/proj"


@pytest.mark.parametrize("cmd,expected", [
    # the false positive seen in testing: build files in a subfolder, then cd back
    ("mkdir -p pkg && cd pkg\ncat << 'EOF' > a.py\nx = '/etc/passwd' + '../other'\nEOF\ncd ..\npython -c 'import pkg'", []),
    ("cat << EOF > a.py\nsee /etc/passwd ../other\nEOF\ncat ../x", ["../x"]),     # body skipped, real arg checked
    ("cat <<-END\n\t/etc/x\n\tEND\nls /etc", ["/etc"]),                          # <<- with tab-indented end
    ("cd sub && ls ../x", []),
    ("cd sub && ls ../../x", ["../../x"]),
    ("cd sub; cd ..; cd ..", [".."]),
    ("cd .. && ls", [".."]),
    ("cd /etc", ["/etc"]),
    ("cd", ["cd"]),
    ("cd ~", ["~"]),
    ("cd $HOME", ["$HOME"]),
    ("echo a > ../out", ["../out"]),
    ("git diff --output=../x", ["../x"]),
    ("ls /tmp/x", []),
])
def test_outside_scope_follows_cd_and_skips_heredocs(cmd, expected):
    assert paths_outside_scope(cmd, ROOT) == expected
