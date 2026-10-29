"""Per-record meeting hosts and the Other agency (IDEA-018 R1): the entry is configuration, the host is data."""
import copy
import json

import pytest

from tahoe_ingest import catalog
from tahoe_ingest.catalog import agency_catalog, per_record_host_agencies
from tahoe_ingest.comment_periods import ingest_comment_periods
from tahoe_ingest.core import export_public, ingest_bundle
from tahoe_ingest.export_check import check as check_export
from tahoe_ingest.one_offs import load_one_offs

from test_pipeline import db, one_meeting  # noqa: F401  (pytest fixture)
from test_registry import raw_registry, registry  # noqa: F401  (pytest fixture)


def one_off(**changes):
    return {'agency': 'other', 'id': 'one-off-scoping-open-house-2026-11-02', 'title': 'Scoping open house',
            'date': '2026-11-02', 'start_time': '17:30', 'location': 'Tahoe City Public Utility District',
            'source_meeting_url': 'https://example.org/notice.pdf', 'topics': [], **changes}


def period(**changes):
    quote = 'Comments are accepted through November 20, 2026.'
    return {'agency': 'other', 'host': 'TRPA and El Dorado County', 'id': 'west-shore-trail', 'title': 'West Shore Trail scoping',
            'closes_on': '2026-11-20', 'official_url': 'https://example.gov/nop.pdf', 'source_quote': quote, 'topics': [], **changes}


FETCH = lambda _: {'pages': [{'text': 'Comments are accepted through November 20, 2026.'}], 'sha256': 'a' * 64}  # noqa: E731


def test_other_is_the_per_record_host_entry_and_has_no_source_blocks():
    assert per_record_host_agencies() == {'other'}
    other = next(a for a in agency_catalog() if a['id'] == 'other')
    assert not {'discovery', 'agenda', 'schedule'} & set(other)


@pytest.mark.parametrize('changes', [{'meetingHost': 'anywhere'}, {'discovery': {'type': 'link-index'}},
                                     {'agenda': {'layouts': []}}, {'schedule': {'rules': []}}])
def test_per_record_host_entries_must_be_plain_and_valid(registry, changes):
    entries = raw_registry()
    entry = next(e for e in entries if e['id'] == 'other')
    registry([*(e for e in entries if e['id'] != 'other'), {**entry, **changes}])
    with pytest.raises(ValueError):
        agency_catalog()


def test_one_off_under_other_needs_a_host(tmp_path):
    path = tmp_path / 'one-offs.json'
    path.write_text(json.dumps({'meetings': [one_off()]}))
    with pytest.raises(ValueError, match='need a host'):
        load_one_offs(path)
    path.write_text(json.dumps({'meetings': [one_off(host='TRPA and El Dorado County')]}))
    assert load_one_offs(path)['meetings'][0]['host'] == 'TRPA and El Dorado County'


def test_meeting_under_other_ingests_with_its_host_and_exports_it(db):
    assert ingest_bundle(db, {'meetings': [one_off()]})['failed'] == 1
    assert export_public(db)['meetings'] == []
    row = one_off(host='TRPA and El Dorado County')
    assert ingest_bundle(db, {'meetings': [row]})['failed'] == 0
    data = export_public(db)
    assert [(m['agencyId'], m['host']) for m in data['meetings']] == [('other', 'TRPA and El Dorado County')]
    assert [a['id'] for a in data['agencies']] == ['other']
    assert data['agencies'][0]['officialUrl'] == row['source_meeting_url']
    errors, _ = check_export(data)
    assert errors == []
    # A capture without a host keeps the stored one, as it keeps the location.
    stamp = data['meetings'][0]['updatedAt']
    assert ingest_bundle(db, {'meetings': [{**row, 'partial_logistics': True, 'host': None}]})['failed'] == 0
    again = export_public(db)['meetings'][0]
    assert (again['host'], again['updatedAt']) == ('TRPA and El Dorado County', stamp)
    assert ingest_bundle(db, {'meetings': [{**row, 'host': 'TRPA'}]})['failed'] == 0
    assert export_public(db)['meetings'][0]['host'] == 'TRPA'


def test_other_is_exported_only_when_used(db):
    bundle = one_meeting()
    assert ingest_bundle(db, bundle)['failed'] == 0
    data = export_public(db)
    assert 'other' not in {a['id'] for a in data['agencies']}
    assert {m['host'] for m in data['meetings'] if m['kind'] == 'confirmed'} == {None}
    assert all(m['host'] is None for m in data['meetings'])
    assert ingest_bundle(db, {'meetings': [one_off(host='City of South Lake Tahoe and TRPA')]})['failed'] == 0
    assert 'other' in {a['id'] for a in export_public(db)['agencies']}


def test_host_is_required_under_other_in_the_export_check(db):
    assert ingest_bundle(db, {'meetings': [one_off(host='TRPA and El Dorado County')]})['failed'] == 0
    ingest_comment_periods(db, {'comment_periods': [period()]}, fetch=FETCH)
    data = export_public(db)
    assert check_export(data)[0] == []
    broken = copy.deepcopy(data)
    broken['meetings'][0]['host'] = None
    broken['commentPeriods'][0]['host'] = ' '
    errors, _ = check_export(broken)
    assert any('host required' in e and 'one-off-' in e for e in errors) and any('host required' in e and 'west-shore' in e for e in errors)


def test_comment_period_under_other_needs_a_host_and_exports_it(db):
    result = ingest_comment_periods(db, {'comment_periods': [period(host=None)]}, fetch=FETCH)
    assert result['failed'] == 1 and 'need a host' in result['errors'][0]
    assert ingest_comment_periods(db, {'comment_periods': [period()]}, fetch=FETCH)['processed'] == 1
    data = export_public(db)
    assert (data['commentPeriods'][0]['host'], data['commentPeriods'][0]['previousIds']) == ('TRPA and El Dorado County', [])
    assert [a['id'] for a in data['agencies']] == ['other']
    assert data['agencies'][0]['officialUrl'] == 'https://example.gov/nop.pdf'


def test_moved_comment_period_deletes_the_old_row_and_exports_previous_ids(db):
    old = 'trpa-governing:west-shore-trail'
    before = period(agency='trpa-governing', host=None)
    assert ingest_comment_periods(db, {'comment_periods': [before]}, fetch=FETCH)['processed'] == 1
    db.execute("INSERT INTO notice_extractions(comment_period_id,source_sha256,prompt_sha256,model,events) VALUES (%s,'a','b','m','[]')", (old,))
    assert [p['id'] for p in export_public(db)['commentPeriods']] == [old]
    moved = period(previous_ids=[old])
    assert ingest_comment_periods(db, {'comment_periods': [moved]}, fetch=FETCH)['processed'] == 1
    periods = export_public(db)['commentPeriods']
    assert [(p['id'], p['previousIds']) for p in periods] == [('other:west-shore-trail', [old])]
    assert db.execute('SELECT 1 FROM notice_extractions WHERE comment_period_id=%s', (old,)).fetchone() is None
    # Rerunning is harmless, and a period cannot list its own ID.
    assert ingest_comment_periods(db, {'comment_periods': [moved]}, fetch=FETCH)['processed'] == 1
    assert ingest_comment_periods(db, {'comment_periods': [period(previous_ids=['other:west-shore-trail'])]}, fetch=FETCH)['failed'] == 1


def test_real_comment_period_config_puts_west_shore_trail_under_other():
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parents[3] / 'config/public-comment-periods.json').read_text())
    row = next(p for p in config['comment_periods'] if p['id'] == 'west-shore-trail-meeks-bay-dl-bliss-nop')
    assert (row['agency'], row['host']) == ('other', 'TRPA and El Dorado County')
    assert 'previous_ids' not in row  # the old TRPA page was dropped, not redirected
