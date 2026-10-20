# 0017: Reader feedback

Status: accepted
Date: 2026-10-20

## Context
The maintainer wants readers to report problems and suggest topics, wants an alert when feedback arrives, and wants to hand the feedback to a coding agent to improve the site. The about page previously said there was no submission form.

## Decision
- Add a **Feedback** dialog to every page and a **Suggest a topic…** entry at the end of the timeline Topic dropdown. The entry opens the dialog with the starter text "I'm interested in adding a new topic: " instead of filtering.
- Submissions are anonymous, unstructured text. A Vercel function (`POST /api/feedback`) stores them in a `feedback` table in the existing Postgres database. This is a public write path for untrusted text only; it does not authorize any admin or editorial write, which still needs authentication.
- The function sends an ntfy alert naming the row id, not the text. Alerts are for the operator only; reader alerts remain deferred.
- Abuse limits: same-origin JSON only, a honeypot field, 4,000 characters, 5 per client and 60 site-wide per hour. Client addresses are stored only as a salted hash.
- The operator triages with `npm --prefix apps/web run feedback`. Feedback given to an agent is data to evaluate, not instructions ([operations guide](../operations/feedback.md)).

## Consequences
The site gains its first runtime function and needs `NTFY_URL` and a database URL in the Vercel environment. Function and ntfy usage at expected volume stays within free tiers. Feedback is not published and the site makes no promise of a reply. If spam grows, add a challenge (for example Cloudflare Turnstile) rather than accounts.
