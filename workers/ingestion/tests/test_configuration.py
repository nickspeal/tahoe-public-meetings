"""Configuration boundaries and operator-maintained meeting notices."""
import json
from pathlib import Path

import pytest

from tahoe_ingest.catalog import agency_catalog, expand_topics
from tahoe_ingest.cli import main
from tahoe_ingest.one_offs import load_one_offs
from tahoe_ingest.topics import classify


def test_csv_rows_remain_distinct_unverified_research():
    agencies = agency_catalog()
    imported = [a for a in agencies if a.get('legacy')]
    assert len(imported) == 41
    assert {a['legacy']['sourceRow'] for a in imported} == set(range(3, 44))
    assert all(a['legacy']['verification'] == 'unverified' for a in imported)
    assert len(agencies) == 45  # Imported rows, Truckee, two added parks commissions, and the TTD RTCPC.
    assert {'slt', 'ttd', 'truckee'} <= {a['id'] for a in agencies if a.get('discoveryAdapter')}
    assert {a['id'] for a in agencies if not a['follow']} == {'douglas-newsletter','fows','lahontan-office','tahoe-fund','cal-epa','usace'}
    # Subsequent source verification may fill operational URLs; the imported
    # research must preserve the original missing website cell independently.
    assert next(a for a in agencies if a['id'] == 'trpa-governing')['legacy']['website'] == ''


@pytest.mark.parametrize('title,expected', [
    ('Pedestrian crossing improvements', {'biking'}),
    ('Annual road repair program', {'biking'}),
    ('Street resurfacing contract', {'biking'}),
    ('Pothole repairs', {'biking'}),
    ('Vacation Home Rental policy workshop', {'short-term-rentals', 'housing'}),
    ('Short-term rentals ordinance', {'short-term-rentals', 'housing'}),
    ('STRs and VHRs', {'short-term-rentals', 'housing'}),
    ('Glyphosate policy', {'environment', 'hot-topics'}),
    ('Incline Village Mobility Hub', {'transit', 'hot-topics'}),
    ('771 Southwood Blvd workshop', {'hot-topics'}),
    ('Vacancy Tax discussion', {'hot-topics'}),
    ('Palisades redevelopment', {'hot-topics'}),
    ('Palisades Tahoe redevelopment', {'hot-topics'}),
    ("Sheriff's Office recommending the Board receive and file a presentation on Automatic License Plate Recognition systems in El Dorado County.", {'hot-topics'}),
    ('Agreement with Flock Group Inc. for ALPR cameras', {'hot-topics'}),
    ('Surveillance Technology Use Policy', {'hot-topics'}),
    ('Security cameras at the recreation center', {'hot-topics'}),
    ('West Nile virus mosquito surveillance update', set()),
    ('Routine budget approval', set()),
    ('Bus service update', {'transit'}),
    ('Resolution in Opposition to the Wildfire Prevention Initiative (Measure S)', {'elections', 'environment'}),
    ('Council Support of School District Measure P', {'elections'}),
    ('Implementation of Measure K Road Maintenance Sales Tax', {'biking'}),
    ('Election consolidation request', {'elections'}),
])
def test_requested_topic_boundaries(title, expected):
    assert set(classify('slt', title, [])) == expected


def test_inclusion_hides_retired_topics():
    assert expand_topics({'short-term-rentals', 'roads'}) == {'short-term-rentals', 'housing'}


def test_agency_topic_hints_apply_only_to_that_agency():
    assert classify('ttd-rtcpc', 'Fleet replacement plan', [])['transit']['rule'] == 'ttd-fleet'
    assert classify('ttd', 'Route 50 service change', [])['transit']['rule'] == 'ttd-service-action'
    assert classify('ttd', 'Bus fleet replacement', [])['transit']['rule'] == 'transit-service'  # catalog patterns come first
    assert classify('slt', 'Fleet replacement plan', []) == {}


def test_agency_topic_hints_must_name_catalog_topics(tmp_path, monkeypatch):
    from tahoe_ingest import catalog, topics
    agencies = json.loads(catalog.agency_catalog_path().read_text())
    next(a for a in agencies if a['id'] == 'ttd')['topicHints'] = {'transport': [{'rule': 'ttd-fleet', 'pattern': 'fleet'}]}
    path = tmp_path / 'agencies.json'
    path.write_text(json.dumps(agencies))
    monkeypatch.setattr(catalog, 'agency_catalog_path', lambda: path)
    catalog.agency_catalog.cache_clear()
    topics.agency_hints.cache_clear()
    try:
        with pytest.raises(ValueError, match='invalid topicHints: ttd'):
            topics.agency_hints()
    finally:
        monkeypatch.undo()
        catalog.agency_catalog.cache_clear()
        topics.agency_hints.cache_clear()


def notice():
    return {'agency': 'slt', 'id': 'one-off-housing-workshop-test',
            'title': 'Housing policy workshop', 'date': '2026-11-12',
            'source_meeting_url': 'https://example.org/official-workshop',
            'topics': ['short-term-rentals']}


def test_one_off_validation_and_dry_run_without_database(tmp_path, capsys):
    path = tmp_path / 'notices.json'
    path.write_text(json.dumps({'meetings': [notice()]}))
    bundle = load_one_offs(path)
    assert not bundle['meetings'][0]['items_complete']
    assert 'start_time' not in bundle['meetings'][0]
    assert main(['ingest-one-offs', str(path), '--dry-run']) == 0
    assert 'one-off-housing-workshop-test' in capsys.readouterr().out


@pytest.mark.parametrize('changes', [
    {'status': 'adopted'}, {'agency': 'unknown'}, {'id': 'regular-meeting-id'}, {'topics': ['roads']},
    {'source_meeting_url': 'javascript:alert(1)'}, {'start_time': '25:00'},
    {'date': '2026-11-01', 'start_time': '01:30'},
])
def test_invalid_one_offs_fail_before_writes(tmp_path, changes):
    path = tmp_path / 'notices.json'
    path.write_text(json.dumps({'meetings': [{**notice(), **changes}]}))
    with pytest.raises(ValueError):
        load_one_offs(path)


def test_unimplemented_discovery_is_explicit(tmp_path):
    with pytest.raises(SystemExit, match='no discovery adapter'):
        main(['discover', 'ltbmu', '--out', str(tmp_path / 'out.json')])
    assert not (tmp_path / 'out.json').exists()
