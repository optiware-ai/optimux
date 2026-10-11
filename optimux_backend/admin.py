"""Command-line setup for Optimux (Feature 24), used by install.sh and the
`optimux` command. It reuses the same code as the Dashboard, so the rules are
the same in both places.

    python -m optimux_backend.admin <command> [args]

Secrets (passwords, API keys) are read from standard input, never from the
command line, so they don't end up in shell history or `ps`.
Exit codes: 0 ok, 1 refused (the message says why), 2 usage error.
"""
import argparse
import json
import os
import sys


def _out(data) -> None:
    print(json.dumps(data) if not isinstance(data, str) else data)


def _secret_from_stdin(what: str) -> str:
    value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        raise SystemExit(f"No {what} given on standard input.")
    return value


def cmd_init(args) -> None:
    """Create ~/.optimux (or OPTIMUX_HOME) with its layout, the access token,
    the database and the starter files. Safe to run again."""
    from optimux_backend.core import paths
    paths.prepare_home()
    from optimux_backend.core import security, store, skills
    from optimux_backend.core.memory_manager import init_memory
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):     # don't print the token here
        security.get_or_create_token()
    init_memory()
    skills.seed_starters()
    store.connect().close()
    _out({"home": paths.HOME})


def cmd_token(args) -> None:
    """Print the access token (for the first sign-in or scripts)."""
    from optimux_backend.core import security
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        token = security.get_or_create_token()
    _out(token)


def cmd_set_password(args) -> None:
    """Set the sign-in password (read from stdin) and sign out every browser."""
    from optimux_backend.core import auth
    password = _secret_from_stdin("password")
    try:
        auth.set_password(password)
    except auth.AuthError as e:
        raise SystemExit(str(e))
    auth.end_all_sessions()
    _out("Password set.")


def cmd_reset_password(args) -> None:
    """Forget the password and two-step codes; the next visit asks for a new one."""
    from optimux_backend.core import auth
    try:
        os.remove(auth.auth_file())
    except FileNotFoundError:
        pass
    auth.end_all_sessions()
    _out("Password removed. Set a new one with the access token on the sign-in page.")


def cmd_set_key(args) -> None:
    """Save an API key (read from stdin) as ENV_KEY in secrets.env."""
    from optimux_backend.core.config_manager import ApiKeyPayload, save_api_key_to_env
    from optimux_backend.core.security import ValidationError
    try:
        save_api_key_to_env(ApiKeyPayload(env_key=args.env_key, api_key=_secret_from_stdin("API key")))
    except ValidationError as e:
        raise SystemExit(str(e))
    _out(f"{args.env_key} saved.")


def cmd_add_model(args) -> None:
    from optimux_backend.core.config_manager import ModelPayload, save_model
    from optimux_backend.core.security import ValidationError
    try:
        save_model(ModelPayload(id=args.id, name=args.name, env_key=args.env_key or ""))
    except ValidationError as e:
        raise SystemExit(str(e))
    _out(f"Model {args.id} added.")


def cmd_set_routing(args) -> None:
    from optimux_backend.core.config_manager import RoutingPayload, save_routing_config
    from optimux_backend.core.security import ValidationError
    try:
        save_routing_config(RoutingPayload(conductor_model=args.conductor, simple_model=args.simple,
                                           complex_model=args.complex))
    except ValidationError as e:
        raise SystemExit(str(e))
    _out("Routing saved.")


def _update_settings(**changes) -> None:
    from optimux_backend.core.config_manager import update_settings
    from optimux_backend.core.security import ScopeError, ValidationError
    try:
        update_settings(**changes)
    except (ValidationError, ScopeError) as e:
        raise SystemExit(str(e))


def cmd_set_workspace(args) -> None:
    path = os.path.abspath(os.path.expanduser(args.path))
    if args.create:
        os.makedirs(path, exist_ok=True)
    _update_settings(projects_root=path, active_project="")
    _out(f"Workspace folder: {path}")


def cmd_sandbox(args) -> None:
    """Turn the sandbox on or off (on is refused when bubblewrap doesn't work)."""
    _update_settings(sandbox=(args.state == "on"))
    _out(f"Sandbox {args.state}.")


def cmd_sandbox_check(args) -> None:
    from optimux_backend.core import sandbox
    _out(sandbox.status(refresh=True))


def cmd_test_model(args) -> None:
    """One tiny real call (a fraction of a cent) to check a model and its key."""
    import litellm
    litellm.suppress_debug_info = True
    from optimux_backend.core import providers
    from optimux_backend.core.config_manager import litellm_key_kwargs, model_id_problem
    problem = model_id_problem(args.id)
    if problem:
        raise SystemExit(problem)
    try:
        litellm.completion(model=args.id, messages=[{"role": "user", "content": "Reply with: ok"}],
                           max_tokens=5, timeout=30, **litellm_key_kwargs(args.id))
    except Exception as e:
        raise SystemExit(providers.classify(e).summary)
    _out("Works.")


def cmd_set_github(args) -> None:
    """Check a GitHub token (read from stdin) with GitHub and save it."""
    from optimux_backend.core import workspace
    from optimux_backend.core.security import ScopeError, ValidationError
    try:
        info = workspace.connect_github(_secret_from_stdin("GitHub token"))
    except (workspace.GitHubError, ValidationError, ScopeError) as e:
        raise SystemExit(str(e))
    _out({"login": info["login"], "warning": info["warning"], "hint": info["hint"], "identity_set": info["identity_set"]})


def cmd_github_status(args) -> None:
    """Is the GitHub CLI there, and is a sign-in available? (no network, no secrets)"""
    from optimux_backend.core import workspace
    _out(workspace.github_status())


def cmd_remove_github(args) -> None:
    from optimux_backend.core import workspace
    from optimux_backend.core.security import ScopeError, ValidationError
    try:
        workspace.disconnect_github()
    except (ValidationError, ScopeError) as e:
        raise SystemExit(str(e))
    _out("GitHub token removed. A running Optimux keeps using it until it restarts (optimux restart).")


def cmd_status(args) -> None:
    """What is configured (no secrets), as JSON."""
    from optimux_backend.core import auth, paths, sandbox
    from optimux_backend.core.config_manager import get_all_models, get_routing_config, get_settings
    settings = get_settings()
    models = get_all_models()
    _out({
        "home": paths.HOME,
        "password_set": auth.password_set(),
        "totp": auth.totp_enabled(),
        "server_mode": auth.server_mode(),
        "workspace": settings.get("projects_root", ""),
        "sandbox": sandbox.enabled(settings),
        "github": settings.get("github_login", "") or None,
        "routing": get_routing_config(),
        "models": {mid: {"name": m.get("name", mid), "key_set": bool(not m.get("env_key") or os.environ.get(m["env_key"]))}
                   for mid, m in models.items()},
    })


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m optimux_backend.admin", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the data folder, token and database").set_defaults(fn=cmd_init)
    sub.add_parser("token", help="print the access token").set_defaults(fn=cmd_token)
    sub.add_parser("set-password", help="set the password (stdin)").set_defaults(fn=cmd_set_password)
    sub.add_parser("reset-password", help="remove the password and two-step codes").set_defaults(fn=cmd_reset_password)
    s = sub.add_parser("set-key", help="save an API key (stdin)")
    s.add_argument("env_key")
    s.set_defaults(fn=cmd_set_key)
    s = sub.add_parser("add-model", help="add or update a model")
    s.add_argument("id"); s.add_argument("name"); s.add_argument("env_key", nargs="?", default="")
    s.set_defaults(fn=cmd_add_model)
    s = sub.add_parser("set-routing", help="Conductor, Simple and Complex models")
    s.add_argument("conductor"); s.add_argument("simple"); s.add_argument("complex")
    s.set_defaults(fn=cmd_set_routing)
    s = sub.add_parser("set-workspace", help="the folder that holds your projects")
    s.add_argument("path"); s.add_argument("--create", action="store_true")
    s.set_defaults(fn=cmd_set_workspace)
    s = sub.add_parser("sandbox", help="turn the sandbox on or off")
    s.add_argument("state", choices=("on", "off"))
    s.set_defaults(fn=cmd_sandbox)
    sub.add_parser("sandbox-check", help="does bubblewrap work here?").set_defaults(fn=cmd_sandbox_check)
    s = sub.add_parser("test-model", help="one tiny real call")
    s.add_argument("id")
    s.set_defaults(fn=cmd_test_model)
    sub.add_parser("set-github", help="check and save a GitHub token (stdin)").set_defaults(fn=cmd_set_github)
    sub.add_parser("github-status", help="GitHub CLI and sign-in (JSON, no secrets)").set_defaults(fn=cmd_github_status)
    sub.add_parser("remove-github", help="forget the saved GitHub token").set_defaults(fn=cmd_remove_github)
    sub.add_parser("status", help="what is configured (JSON, no secrets)").set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
