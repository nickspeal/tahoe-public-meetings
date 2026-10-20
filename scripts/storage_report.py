#!/usr/bin/env python3
"""Read-only database storage report: sizes, page text, citation coverage.

Uses DATABASE_URL (process environment, then repository .env, then the local
default), like scripts/project.py. Issues SELECT statements only.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LOCAL_URL = "postgresql://tahoe:tahoe-local@127.0.0.1:55432/tahoe"


def database_url():
    env = dict(os.environ)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for raw in dotenv.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip("\"'"))
    return env.get("DATABASE_URL", LOCAL_URL)


def query(url, sql):
    """Return rows as dicts. Prefers psycopg; falls back to the psql CLI."""
    wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t"
    try:
        import psycopg
    except ImportError:
        env = dict(os.environ, PGOPTIONS="-c default_transaction_read_only=on")
        out = subprocess.run(
            ["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", url, "-c", wrapped],
            capture_output=True, text=True, check=True, env=env).stdout
        return json.loads(out)
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as conn:
        return conn.execute(wrapped).fetchone()[0]


# A citation points at (documentId, page). documentId is document_versions.id.
CITES = """
with cites as (
  select (c->>'documentId') as doc, (c->>'page')::int as page, c->>'quote' as quote
  from (select citations from agenda_items union all select citations from summaries
        union all select citations from summary_versions) s,
       jsonb_array_elements(s.citations) c
  where c ? 'documentId' and c ? 'page'
)
"""

QUERIES = {
    "database_bytes": "select pg_database_size(current_database()) as bytes",
    "tables": """select c.relname as name, pg_total_relation_size(c.oid) as total_bytes,
        pg_relation_size(c.oid) as heap_bytes, pg_indexes_size(c.oid) as index_bytes,
        coalesce(pg_total_relation_size(nullif(c.reltoastrelid,0)),0) as toast_bytes,
        greatest(c.reltuples,0)::bigint as est_rows
        from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relkind='r' order by 2 desc""",
    "pages": """select count(*) as rows, coalesce(sum(octet_length(text)),0) as text_bytes,
        coalesce(avg(octet_length(text)),0)::int as avg_bytes_per_page,
        coalesce(percentile_cont(0.5) within group (order by octet_length(text)),0)::int as median_bytes,
        coalesce(max(octet_length(text)),0) as max_bytes,
        pg_total_relation_size('document_pages') as table_total_bytes,
        coalesce(pg_total_relation_size('document_pages')::float / nullif(count(*),0),0)::int as total_bytes_per_page
        from document_pages""",
    "agencies": """select a.id as agency,
        (select count(*) from meetings m where m.agency_id=a.id) as meetings,
        count(distinct v.id) as documents, count(p.page_number) as pages,
        coalesce(sum(octet_length(p.text)),0) as text_bytes
        from agencies a left join meetings m on m.agency_id=a.id
        left join document_versions v on v.meeting_id=m.id
        left join document_pages p on p.document_version_id=v.id
        group by a.id order by text_bytes desc""",
    "pages_per_document": """select count(*) as documents, coalesce(min(n),0) as min,
        coalesce(percentile_cont(0.5) within group (order by n),0) as median,
        coalesce(percentile_cont(0.9) within group (order by n),0) as p90,
        coalesce(max(n),0) as max from (select document_version_id, count(*) n
        from document_pages group by 1) d""",
    "citation_coverage": CITES + """, cited as (select distinct doc, page from cites)
        select (select count(*) from document_pages) as pages,
        (select count(*) from document_pages p join cited c
           on c.doc=p.document_version_id and c.page=p.page_number) as cited_pages,
        (select coalesce(sum(octet_length(p.text)),0) from document_pages p join cited c
           on c.doc=p.document_version_id and c.page=p.page_number) as cited_text_bytes""",
    "citation_quotes": CITES + """, uniq as (select distinct doc, page, quote from cites)
        select count(*) as citations, coalesce(sum(octet_length(quote)),0) as quote_bytes from uniq""",
    "summaries": """select
        (select count(*) from summaries) as summaries_rows,
        (select coalesce(sum(octet_length(text)),0) from summaries) as summaries_text_bytes,
        pg_total_relation_size('summaries') as summaries_total_bytes,
        (select count(*) from summary_versions) as versions_rows,
        (select coalesce(sum(octet_length(text)),0) from summary_versions) as versions_text_bytes,
        pg_total_relation_size('summary_versions') as versions_total_bytes,
        (select count(*) from agenda_items) as agenda_items""",
}


def mb(n):
    return f"{n / 1e6:,.2f} MB"


def collect(url):
    return {k: query(url, v) for k, v in QUERIES.items()}


def render(r):
    p = r["pages"][0]
    cov = r["citation_coverage"][0]
    d = r["pages_per_document"][0]
    s = r["summaries"][0]
    q = r["citation_quotes"][0]
    out = [f"Database total: {mb(r['database_bytes'][0]['bytes'])}", "", "Tables (total = heap + indexes + TOAST):"]
    for t in r["tables"]:
        out.append(f"  {t['name']:<20} {mb(t['total_bytes']):>12}  heap {t['heap_bytes']:>10,}  idx {t['index_bytes']:>9,}  toast {t['toast_bytes']:>9,}  ~{t['est_rows']:,} rows")
    out += ["", "document_pages:",
            f"  rows {p['rows']:,}; text {p['text_bytes']:,} bytes ({mb(p['text_bytes'])}); avg {p['avg_bytes_per_page']:,} B/page; median {p['median_bytes']:,}; max {p['max_bytes']:,}",
            f"  table incl. index/TOAST {mb(p['table_total_bytes'])} = {p['total_bytes_per_page']:,} B/page on disk",
            "", "Per agency:", f"  {'agency':<14}{'meetings':>9}{'docs':>6}{'pages':>7}{'text bytes':>13}"]
    for a in r["agencies"]:
        out.append(f"  {a['agency']:<14}{a['meetings']:>9}{a['documents']:>6}{a['pages']:>7}{a['text_bytes']:>13,}")
    out += ["", f"Pages per document ({d['documents']} docs): min {d['min']:.0f}, median {d['median']:.0f}, p90 {d['p90']:.0f}, max {d['max']:.0f}"]
    share = cov["cited_pages"] / cov["pages"] if cov["pages"] else 0
    tshare = cov["cited_text_bytes"] / p["text_bytes"] if p["text_bytes"] else 0
    out += [f"Cited pages: {cov['cited_pages']:,} of {cov['pages']:,} ({share:.1%}); cited page text {cov['cited_text_bytes']:,} bytes ({tshare:.1%} of text)",
            f"Distinct citation quotes: {q['citations']:,}, {q['quote_bytes']:,} quote bytes",
            "", f"Summaries: {s['summaries_rows']:,} rows, text {s['summaries_text_bytes']:,} B, table {mb(s['summaries_total_bytes'])}",
            f"Summary versions: {s['versions_rows']:,} rows, text {s['versions_text_bytes']:,} B, table {mb(s['versions_total_bytes'])}",
            f"Agenda items: {s['agenda_items']:,}"]
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    report = collect(database_url())
    print(json.dumps(report, indent=2) if args.json else render(report))


if __name__ == "__main__":
    main()
