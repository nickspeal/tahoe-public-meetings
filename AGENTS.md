# Project instructions

Read `docs/planning/implementation-handoff.md` first. It is the current scope and technical handoff; older service-comparison documents preserve research, not alternative instructions to implement.

If `private/AGENTS.private.md` exists, read it too. It holds maintainer-only instructions and is never committed. Never copy its contents, or anything else from `private/`, into tracked files or commit messages.

- Selected stack: Vercel website, Prisma Postgres, Python ingestion on Google Cloud Run Jobs, Cloud Scheduler. Deploy only to the project's designated Vercel scope; verify the authenticated identity and project ownership before linking, creating, or deploying.
- PDFs are temporary processing inputs. Persist extracted text/provenance and link to official documents; no permanent PDF archive in initial versions.
- Automatic source-backed summaries need no human approval. There is one automated summarization system with a single editorial voice (decision 0010); the maintainer steers it over time, there are no attributed personal perspective notes, and the public sees only the latest summary.
- Use the shared global topic catalog across the app; topics can expand per decision 0008. Alerts and resident accounts are deferred. Total recurring budget is below $20/month.
- Track new ideas in `docs/ideas/` and consequential choices in `docs/decisions/`. Distinguish accepted decisions from proposed defaults.
- The repository is public and MIT licensed (decision 0016). Commit and push to `origin/main` as work progresses, but treat everything committed, including commit messages, as permanently public: no secrets, personal notes, or private contact details.
- Refer to people by role ("the maintainer", "the operator") in docs, code, and commit messages, not by name.
