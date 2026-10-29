"""Public comment windows are participation opportunities, not multi-day meetings."""
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

from .catalog import followed_agencies, topic_ids, expand_topics
from .http import public_url
from .source import download_extract


def ingest_comment_periods(conn, bundle: dict, *, fetch=download_extract) -> dict:
    from .core import iso, now
    result = {'processed': 0, 'failed': 0, 'errors': []}
    for row in bundle.get('comment_periods', []):
        try:
            agency = row['agency']
            if agency not in followed_agencies():
                raise ValueError('unknown or unfollowed agency')
            if not row['id'].strip() or not row['title'].strip():
                raise ValueError('comment period needs a stable ID and title')
            url = public_url(row['official_url'])
            if row.get('participation_url'):
                public_url(row['participation_url'])
            closes = date.fromisoformat(row['closes_on'])
            opens = date.fromisoformat(row['opens_on']) if row.get('opens_on') else None
            if opens and opens > closes:
                raise ValueError('comment period opens after its closing date')
            deadline = datetime.fromisoformat(row['closes_at'].replace('Z', '+00:00')) if row.get('closes_at') else None
            if deadline and (deadline.tzinfo is None or deadline.astimezone(ZoneInfo('America/Los_Angeles')).date() != closes):
                raise ValueError('exact deadline requires an offset and must match closes_on in Pacific time')
            tags = row.get('topics', [])
            if not isinstance(tags, list) or not set(tags) <= topic_ids():
                raise ValueError('unknown comment period topic')
            captured = fetch(url)
            text = '\n\n'.join(page['text'] for page in captured['pages'])
            quote = row['source_quote'].strip()
            if not quote or quote not in text:
                raise ValueError('comment period source quote is absent from fetched official text')
            # Project details and the summary are copied from the notice; line
            # breaks may differ from the extracted text, other characters may not.
            flat = ' '.join(text.split())
            details = {}
            for field in ('project_title', 'project_proponent', 'summary'):
                value = ' '.join((row.get(field) or '').split()) or None
                if value and value not in flat:
                    raise ValueError(f'comment period {field} is absent from fetched official text')
                details[field] = value
            # Dates/instructions are explicitly recorded from the notice, never
            # inferred from a regular meeting schedule or generic comment button.
            stamp = now()
            with conn.transaction():
                conn.execute('''INSERT INTO comment_periods
                    (id,agency_id,source_id,title,opens_on,closes_on,closes_at,official_url,participation_url,
                     instructions,topics,source_quote,source_text,source_sha256,checked_at,
                     project_title,project_proponent,summary)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title,opens_on=excluded.opens_on,
                    closes_on=excluded.closes_on,closes_at=excluded.closes_at,official_url=excluded.official_url,
                    participation_url=excluded.participation_url,instructions=excluded.instructions,
                    topics=excluded.topics,source_quote=excluded.source_quote,source_text=excluded.source_text,
                    source_sha256=excluded.source_sha256,checked_at=excluded.checked_at,
                    project_title=excluded.project_title,project_proponent=excluded.project_proponent,
                    summary=excluded.summary,
                    updated_at=CASE WHEN (comment_periods.title,comment_periods.opens_on,comment_periods.closes_on,
                      comment_periods.closes_at,comment_periods.source_sha256,comment_periods.topics,
                      comment_periods.instructions,comment_periods.participation_url,comment_periods.official_url,
                      comment_periods.project_title,comment_periods.project_proponent,comment_periods.summary)
                      IS DISTINCT FROM (excluded.title,excluded.opens_on,excluded.closes_on,excluded.closes_at,
                      excluded.source_sha256,excluded.topics,excluded.instructions,excluded.participation_url,excluded.official_url,
                      excluded.project_title,excluded.project_proponent,excluded.summary)
                      THEN now() ELSE comment_periods.updated_at END''',
                    (f'{agency}:{row["id"]}', agency, row['id'], row['title'], opens, closes, deadline,
                     url, row.get('participation_url'), row.get('instructions'), json.dumps(sorted(expand_topics(set(tags)))),
                     quote, text, captured['sha256'], stamp,
                     details['project_title'], details['project_proponent'], details['summary']))
            result['processed'] += 1
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            result['failed'] += 1
            result['errors'].append(f'{row.get("agency")}:{row.get("id")}: {exc}')
    return result


def export_comment_periods(conn) -> list[dict]:
    from .core import iso, _json
    return [{'id': row['id'], 'agencyId': row['agency_id'], 'title': row['title'],
             'opensOn': row['opens_on'].isoformat() if row['opens_on'] else None,
             'closesOn': row['closes_on'].isoformat(), 'closesAt': iso(row['closes_at']),
             'officialUrl': row['official_url'], 'participationUrl': row['participation_url'],
             'instructions': row['instructions'], 'projectTitle': row['project_title'],
             'projectProponent': row['project_proponent'], 'summary': row['summary'], 'topics': sorted(expand_topics(set(_json(row['topics'])))),
             'sourceQuote': row['source_quote'], 'sourceSha256': row['source_sha256'],
             'lastCheckedAt': iso(row['checked_at']), 'updatedAt': iso(row['updated_at'])}
            for row in conn.execute('SELECT * FROM comment_periods ORDER BY closes_on,id')
            if row['agency_id'] in followed_agencies()]
