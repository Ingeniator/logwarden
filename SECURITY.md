# Security policy

## Supported versions

logwarden is at 0.x. Security fixes go into the latest release only; upgrade to get them.

| Version | Supported |
|---|---|
| Latest 0.x release | ✅ |
| Older releases | ❌ |

## Reporting a vulnerability

Please report it privately through GitHub:
[Report a vulnerability](https://github.com/Ingeniator/logwarden/security/advisories/new).
Do not open a public issue.

Include the logwarden version, how you run it (PyPI package, Docker image, GitHub Action,
GitLab template, MCP server) and the steps to reproduce it. Do not include real secrets or
personal data from your logs; use made-up values.

We aim to reply within 7 days and to publish a fixed release and an advisory once a fix is ready.

## Scope

In scope: the `logwarden` package, the Docker image `ghcr.io/ingeniator/logwarden`, the GitHub
Action, the GitLab template and the Claude Code plugin in this repository. Examples:

- logwarden writes a value it found in a log (a secret, an email address) into its output, a
  report or a findings file, when it should only name the field.
- A downloaded file (Weaver, the semantic conventions) is used without its SHA-256 check.
- `logwarden capture` or `logwarden mcp` can be made to read or write files outside what it
  was asked to.

Out of scope: weaknesses in OTel Weaver or other dependencies themselves (report those
upstream), and logs that a sensitive-data rule fails to flag. A missed secret is a rule gap:
open a normal issue with a made-up example.
