# 0010: Single editorial voice
Status: accepted
Date: 2026-10-20
Related ideas: IDEA-001, IDEA-007
Supersedes: [0003: Editorial stance](0003-editorial-stance.md)

## Context
Decision 0003 allowed optional, attributed commentary notes beside automatic summaries. The maintainer's round 2 feedback rejects that: nothing should be described as coming from a personal perspective, and the human editorial layer as built (perspective notes, a public corrections page, visible correction attribution) is not wanted.

## Decision
- Tahoe Public Meetings, as an entity, has one editorial voice. The maintainer shapes it over time through topic selection, classification rules, and steering of the summarization system.
- There is one automated summarization system. The maintainer iterates on its prompts, rules, and topic catalog rather than writing attributed commentary. No attributed personal perspective notes exist in the product.
- The public sees only the latest summary for each item. No summary history, revision log, correction attribution, or public corrections page.
- Operator corrections, suppression, and topic overrides remain as internal steering inputs. A correction replaces the displayed summary text without public attribution; its author and reason are still required by the CLI and kept internally. The `summary_versions` table is an internal audit trail and is never exported.
- The `editorial.perspective` column remains in the schema for compatibility but is no longer read or written.

## Alternatives and consequences
Keeping attributed notes would preserve a commentary channel but conflicts with the single-voice direction. Consequences: simpler public pages, less to maintain, and quality improvements come from improving the system rather than annotating items. Source-backed citations remain the accountability mechanism, along with links to official documents.

## Revisit when
Readers need a visible way to report or see errors, or the maintainer wants a distinctly attributed commentary channel again.
