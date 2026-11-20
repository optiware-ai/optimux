"""Add-ons (v0.8): a separate package plugs into sockets the core leaves open.

The core never imports an add-on by name in its own logic. It asks questions
and sends events; an add-on that is installed answers them. With no add-on
installed every question gets its default, and Optimux is the solo product.

An add-on is a Python package that has `register(api)`. `load()` looks for the
packages in PLUGIN_MODULES, imports the ones that are installed and calls
`register` once. `api` is the only thing an add-on is given:

- `api.add_router(router)`   a FastAPI router, mounted by `mount(app)`;
- `api.answer(question, fn)` the one answer to a question the core asks with
                             `ask(question, ...)`;
- `api.listen(event, fn)`    a listener for an event the core sends with
                             `emit(event, ...)`.

Failing safe, not open:

- A package that is installed but broken (it won't import, has no `register`,
  or `register` raises) stops the start with a PluginError. It is never
  skipped, because an add-on can be the thing that keeps people out of places.
- An answer that raises is not caught: the request fails (a 500), it does not
  fall back to the default. A listener that raises is logged and the others
  still run: events only tell, they never decide.
- `check_mode(mode)` refuses a database that says "team" when the team add-on
  isn't loaded, so team mode never starts without its protections.

No FastAPI imports at module level, so it can be tested alone.
"""
import importlib
import importlib.util
import logging

log = logging.getLogger("optimux.plugins")

PLUGIN_MODULES = ("optimux_team",)
MODE_ADDON = {"team": "optimux_team"}      # a mode that needs an add-on, and which one


class PluginError(RuntimeError):
    """An add-on is broken, or a mode needs an add-on that isn't there."""


class Api:
    """What one add-on is given. Nothing else of the core is reachable through it."""

    def __init__(self, name: str):
        self.name = name
        self.routers: list = []
        self.answers: dict = {}
        self.listeners: dict = {}
        self.public: set = set()

    def add_router(self, router) -> None:
        self.routers.append(router)

    def public_path(self, *paths: str) -> None:
        """Paths reachable without signing in (the add-on's own sign-in pages, which carry their own credential)."""
        self.public.update(paths)

    def answer(self, question: str, fn) -> None:
        self.answers[question] = fn

    def listen(self, event: str, fn) -> None:
        self.listeners.setdefault(event, []).append(fn)


_loaded: dict = {}          # add-on name -> its Api
_answers: dict = {}         # question -> (add-on name, fn)


def loaded() -> list:
    """The names of the add-ons that are loaded."""
    return list(_loaded)


def is_public(path: str) -> bool:
    """Is this path one an add-on has declared reachable without signing in?"""
    return any(path in api.public for api in _loaded.values())


def public_paths() -> set:
    """Every path the loaded add-ons declared public."""
    return set().union(*(api.public for api in _loaded.values()))


def reset() -> None:
    """Forget every add-on (for tests)."""
    _loaded.clear()
    _answers.clear()


def load(names=None) -> list:
    """Import and register every installed add-on once. Returns the loaded names.
    A name that isn't installed is skipped; one that is installed and fails raises."""
    for name in (PLUGIN_MODULES if names is None else names):
        if name in _loaded:
            continue
        try:
            installed = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            installed = False
        if not installed:
            continue
        try:
            module = importlib.import_module(name)
        except Exception as e:
            raise PluginError(f"The add-on {name} is installed but can't be loaded: {e}") from e
        register = getattr(module, "register", None)
        if not callable(register):
            raise PluginError(f"The add-on {name} has no register(api) function.")
        api = Api(name)
        try:
            register(api)
        except Exception as e:
            raise PluginError(f"The add-on {name} failed to register: {e}") from e
        for question in api.answers:
            if question in _answers:
                raise PluginError(f"The add-on {name} answers {question!r}, which {_answers[question][0]} already answers.")
        for question, fn in api.answers.items():
            _answers[question] = (name, fn)
        _loaded[name] = api
    return loaded()


def check_mode(mode: str) -> None:
    """Refuse to start in a mode whose add-on isn't loaded (team mode without
    the team add-on). Solo needs nothing."""
    need = MODE_ADDON.get(mode)
    if need and need not in _loaded:
        raise PluginError(f"This install is in {mode} mode, but the add-on {need} is not installed, "
                          f"so it can't start safely. Install the add-on, or run `optimux disable-{mode}`.")


def ask(question: str, *args, default=None, **kwargs):
    """The add-on's answer to a question, or `default` when no add-on answers it."""
    found = _answers.get(question)
    if found is None:
        return default
    return found[1](*args, **kwargs)


def emit(event: str, **kwargs) -> None:
    """Tell every listener. A listener that fails is logged; the others still run."""
    for api in list(_loaded.values()):
        for fn in api.listeners.get(event, ()):
            try:
                fn(**kwargs)
            except Exception:
                log.exception("add-on %s failed on the event %s", api.name, event)


def mount(app) -> None:
    """Add every loaded add-on's routers to the FastAPI app."""
    for api in _loaded.values():
        for router in api.routers:
            app.include_router(router)
