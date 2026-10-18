# Contributing to Optimux

Thanks for your interest in improving Optimux! This page explains how to set up a development environment and what a good contribution looks like.

> **Pull requests: not yet.** For now, outside pull requests aren't accepted. Optimux may later have paid editions next to this free AGPL one, and that needs a contributor agreement (CLA) in place before outside code can be merged. Until then, the most helpful contributions are **issues**: bug reports, ideas, questions and documentation problems. This section will change when the agreement is ready.

## Getting set up

```bash
git clone https://github.com/optiware-ai/optimux.git
cd optimux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pytest -q
```

The test suite needs no API keys and no network. Install `bubblewrap` to also run the sandbox tests for real. Optimux supports **Python 3.10 and newer**: run the suite on 3.10 too (for example `uv venv -p 3.10`) when you use the standard library in a new way, since newer keyword arguments (such as `shutil.rmtree(dir_fd=...)`, 3.11) fail there.

To try your changes, start the server with `uvicorn optimux_backend.main:app --host 127.0.0.1 --port 8000 --reload`. Point `OPTIMUX_HOME` at a scratch folder if you don't want to touch your own settings and history.

## Reporting bugs and asking for features

- **Bugs**: [open an issue](https://github.com/optiware-ai/optimux/issues/new/choose) with the **Bug report** form. It asks for what you did, what you expected, what happened, how Optimux is installed and its version. Never paste API keys, tokens, passwords or your server's address.
- **Features**: use the **Feature request** form. Describe the problem first, then the change you have in mind. Larger features are discussed before any code is written.
- **Questions and early ideas**: use [Discussions](https://github.com/optiware-ai/optimux/discussions) (**Q&A** and **Ideas**), so answers and votes help everyone.
- **Security issues**: please don't open a public issue; see [SECURITY.md](SECURITY.md).

## Making a change

These are the rules the project itself follows (and that outside pull requests will follow once they are accepted).

1. **Branch** from `main`.
2. **Keep it focused**: one feature or fix per pull request.
3. **Add tests** in `tests/` for new behaviour, and keep `pytest -q` green.
4. **Update the sidecar**: every source file `foo.py` has a `foo.py_AGENT.md` describing it; update it in the same commit.
5. **Update the docs**: user-visible changes belong in [`docs/GUIDE.md`](docs/GUIDE.md). `tests/test_guide.py` checks that defaults quoted there match the code.
6. **Write the commit message** with [`commit-template.txt`](commit-template.txt): `[ TYPE ] Scope: Description`, the files changed, then motivation, changes, validation and deployment notes.

## Versions and releases

The version is in the [`VERSION`](VERSION) file, as `MAJOR.MINOR.PATCH`. Optimux shows it with the exact commit, like `v0.4.0 (5e0673b)`: on Dashboard → System, in snapshots, and with `optimux version`. Between releases the number stays the same and only the commit changes, so you always know precisely what runs. Git tags are not used to find the version: installs are shallow clones and have no tags.

| Digit | Goes up when an update… | Examples |
|---|---|---|
| **MAJOR** | needs the user to act, or removes something: a reinstall, a manual data step, a removed feature, a changed setting format. Rare. | — |
| **MINOR** | adds features or changes behaviour without any action from the user (automatic data upgrades count here). | new model tools; a changed default |
| **PATCH** | only fixes things; there is nothing new to learn. | a wrong label; a layout bug |

When MINOR goes up, PATCH goes back to 0; when MAJOR goes up, both do. Before 1.0.0, Optimux is in beta. 1.0.0 is kept for the moment it is announced as stable.

**Making a release:**

1. Decide that the commits since the last release are ready (daily commits don't change the version).
2. One last commit, `[ CHORE ] Release: vX.Y.Z`, sets `VERSION` and adds an entry at the top of [`CHANGELOG.md`](CHANGELOG.md): what's new, what's fixed, and what users must do, if anything. Update the version in the README badge.
3. Push it, then tag it: `git tag vX.Y.Z && git push origin vX.Y.Z`.

## Design principles

When in doubt, these guide decisions in Optimux:

- **Cost is a feature.** Every token sent on every call must earn its place. Prefer things that cost nothing until used.
- **The user decides.** Expensive or risky actions are visible, explained, and confirmable; defaults are safe.
- **Nothing is lost.** Chats, usage and memory history survive restarts and updates; files are moved to `legacy/`, never deleted.
- **Nothing leaks.** Keys stay in `secrets.env`, never in repositories, prompts or command environments.
- **Plain and readable.** No bundler: the page is plain files; small modules with clear responsibilities.

## Code style

- Python: standard library first, type hints on public functions, short docstrings that say *why*.
- Frontend: templates in `index.html`, logic in `web/app.js`, styles in `web/optimux.css`; libraries are pinned copies in `web/vendor/` (never a CDN). No inline `<script>` or `style="…"`: the Content Security Policy blocks them. Anything shown with `v-html` goes through `optimuxSafeHtml`. Keep explanations in `?` help tips rather than long text on the page.
- Shell (`install.sh`, `scripts/optimux`): bash 4.4+, `set -Eeuo pipefail`, everything in functions (only the last line runs `main`). Mind `set -e`: a function must not end in `[[ x ]] && y`, and `x="$(a | b)"` needs `|| true`; tests check both. Run `shellcheck -x -S warning install.sh scripts/optimux`. Secrets go to `python -m optimux_backend.admin` on stdin only.
- Tailwind: `web/app.css` is built from the classes in the page. After adding classes, run `scripts/build-css.sh` (needs Node.js) and commit the result; a test fails if a class is missing.
- User-facing text: short, concrete, in plain English.
