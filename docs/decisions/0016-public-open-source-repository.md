# 0016: Public open-source repository

Status: accepted
Date: 2026-10-20
Supersedes: the local-only Git constraint in [0006](0006-local-proof-of-concept.md) and the implementation handoff

## Context
Planning and the local proof of concept kept the Git repository local until a license was chosen and private material was separated from distributable source.

## Decision
- The code is public at [github.com/nickspeal/tahoe-public-meetings](https://github.com/nickspeal/tahoe-public-meetings) under the [MIT License](../../LICENSE). Work is committed to `main` and pushed to `origin`.
- Everything tracked, including docs, config, fixtures, and commit messages, is publicly visible. Maintainer notes, credentials, local data, and deployment identity files stay outside Git (`private/`, `.env`, `data/`).
- Docs, code, and commit messages refer to people by role ("the maintainer", "the operator"), not by name.
- Public visibility does not change hosting: cloud resources and deployment remain unprovisioned until separately set up.

## Consequences
Contributors and other communities can reuse the code. Before committing, check that changes contain no secrets, personal notes, or private contact details; a pushed commit should be treated as permanently public.
