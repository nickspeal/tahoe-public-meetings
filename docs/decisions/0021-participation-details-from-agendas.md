# 0021: Read participation details from agendas with Claude

Status: proposed default, implemented (awaiting maintainer review)
Date: 2026-10-27

## Context
Every meeting page has an "Attend & participate" card: location, remote access, public comment instructions, and a comment deadline. No discovery adapter supplied those fields except Truckee's Municode `venue`. In the local corpus (131 confirmed meetings across 35 agencies), no meeting had a remote link, comment instructions, or a deadline, and only 21 had a location. So nearly every card read "Not published," including El Dorado County Board of Supervisors meetings, whose agendas give a Zoom link, two phone lines, a comment email, and a 4:00 PM deadline on page 2.

Nearly every agency states these details in the first pages of its agenda, which the pipeline already stores as page text. The wording and layout differ by agency (Zoom, Teams, Granicus, YouTube, phone conference IDs, venues split across lines), so per-agency regular expressions would be brittle and would need maintenance for each new source.

## Decision
- A new pipeline step, `participation`, runs after `classify-topics` and also exists as `tahoe-ingest extract-participation`. For each meeting dated within the last 14 days or later (or undated), it sends the first three pages and the last page of up to two current agenda documents to Claude Haiku 5.5, with Sonnet 5.5 as the fallback, and asks for location, remote link, watch link, phone line, comment form, comment email, instructions with a supporting quote, and deadline.
- Every returned field must pass deterministic checks against the same text before it is kept. Links, emails, and phone numbers, including meeting IDs, must appear in the source, ignoring line breaks. Deadlines and the instructions quote must appear word for word, ignoring punctuation. Each comma-separated part of a location must appear within one 400-character window. Instructions may not mention any link, email, number, or time that is not in the source. Rejected fields are stored with a reason and never published.
- Results live in `ai_participation`, keyed to the document versions read. The export uses a row only while those versions are still current, so a replaced agenda hides old details until the next run reads the new one. The prompt hash is stored, so a prompt change re-extracts.
- Precedence in the export is: source adapter values, then values read from the agenda, then an agency's standing `participation` block in `packages/domain/agencies.json`, which must cite an official `sourceUrl` and a `checkedOn` date. Truckee's block supplies the Town's "Watch Live & Recorded Meetings" link and its public comment form, because the Municode HTML agenda refers to the form without a usable link.
- The meeting page says when details were read automatically from the agenda and still tells readers to confirm with the agency.

## Cost
- Claude: the first full local run covered 105 meetings for $0.066 on Haiku, about $0.0006 per meeting. After the first run, only new or changed agendas are re-read. Spending counts toward the same $5/month AI budget and per-run `--max-cost` guard as topic classification (decision 0011).
- Database operations (decision 0020): about 4 statements per run, plus 2 per meeting extracted (one result row and one `ai_usage` row). The export adds one bulk read. The first production run reads every meeting in the window once, about 2 statements per meeting.

## Alternatives
- Per-agency parsing rules: no API cost, but brittle across 35 layouts. Kept as a later option for a high-volume agency if Claude results prove unreliable.
- Configuring all participation details by hand per agency: details such as Zoom links change from meeting to meeting. Standing agency details are used only as the last fallback.

## Revisit when
- Validation rejects a meaningful share of fields for an agency (see `ai_participation.rejected`), or readers report a wrong link or deadline.
