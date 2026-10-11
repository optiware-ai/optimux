# Security policy

Optimux runs commands on your machine and keeps API keys, so security reports are taken seriously. Thank you for helping.

## Reporting a vulnerability

**Please don't open a public issue.** Use one of these private channels:

1. **GitHub (preferred):** go to the [Security tab](https://github.com/optiware-ai/optimux/security) and click **Report a vulnerability**. Only the maintainers can see the report.
2. **Email:** optiware.ai@gmail.com, with "Optimux security" in the subject.

Please include:

- what an attacker can do, and what they need first (network access, a signed-in browser, a malicious repository, a prompt injection…);
- the steps to reproduce it, and the Optimux version (`optimux version`, or the commit);
- your setup: local or server mode, sandbox on or off, operating system.

Never send real API keys, tokens or passwords; use made-up values.

## What happens next

- You get an answer within **7 days**.
- Confirmed problems are fixed in a new release as soon as possible; you're told before it is published.
- With your permission, the release notes thank you by name.

Optimux is maintained by a small team, so these are honest targets, not a guarantee.

## Supported versions

Optimux is in beta. Only the **latest release** (and `main`) receives security fixes. Update with `optimux update`.

## Scope

In scope: the Optimux server and web interface, the sandbox, sign-in and sessions, secret handling, the installer and the `optimux` command, and the files in `deploy/`.

Out of scope:

- what an approved command does: Optimux asks before every command (and in server mode always), so a command you approve runs with the permissions the sandbox allows;
- running with the sandbox turned off on purpose, or with auto-approve on;
- problems in the model providers, LiteLLM, or other dependencies themselves (please report those upstream; tell us if Optimux uses them unsafely);
- denial of service from a signed-in user.

The protections and their limits are described in the [Guide's security chapter](docs/GUIDE.md#security).
