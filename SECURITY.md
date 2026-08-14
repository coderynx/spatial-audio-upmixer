# Security Policy

## Supported Versions

This project is pre-1.0 (`0.1.0`) and does not yet maintain separate stable/maintenance branches. Security fixes
are made against `main` only.

## Reporting a Vulnerability

Please report suspected security vulnerabilities privately rather than opening a public issue. Use GitHub's
[private security advisory](../../security/advisories/new) feature on this repository, or contact the maintainer
directly at simone.filippi.1999@gmail.com.

Include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce (a minimal manifest, input file, or API request is ideal).
- Which component is affected: `packages/core`, `apps/cli`, `apps/api`, or `apps/web`.

You should expect an initial response within a few days. Please allow time for a fix to be developed and released
before any public disclosure.

## Scope notes

- `apps/api` accepts user-uploaded audio and manifests; path-traversal, resource-exhaustion, and deserialization
  concerns in the import/job pipeline are in scope.
- `packages/core`'s stem-separation inference engine loads model weights from disk (`--stem-model-dir` /
  `UPMIXER_DATA_DIR`); concerns about untrusted model files are in scope.
- Dependency vulnerabilities in third-party packages should be reported upstream, but flagging them here is
  welcome if they affect this project's usage.
