import io
import json
import ssl
import urllib.error

import pytest

from tahoe_ingest import http, source
from tahoe_ingest.html_source import html_text
from tahoe_ingest.html_agenda import parse_html_agenda
from tahoe_ingest.source_discovery import discover_configured, date_in


def test_tls_failure_uses_verified_native_fallback_but_not_auth_or_rate_limits(monkeypatch):
    def fail(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError('issuer missing'))
    monkeypatch.setattr(http.urllib.request, 'urlopen', fail)
    calls=[]
    monkeypatch.setattr(http, '_native_fetch', lambda *a: (calls.append(a) or (b'%PDF-test', 'application/pdf')))
    assert http.fetch_bytes('https://example.gov/a.pdf', max_bytes=100)[0].startswith(b'%PDF')
    assert len(calls)==1
    for status in [401,429]:
        def error(*a, **k):raise urllib.error.HTTPError('https://example.gov',status,'blocked',{},None)
        monkeypatch.setattr(http.urllib.request,'urlopen',error)
        with pytest.raises(urllib.error.HTTPError):http.fetch_bytes('https://example.gov/a.pdf',max_bytes=100)
    assert len(calls)==1


def test_source_limits_and_s3_path_style(monkeypatch):
    assert http.public_url('https://granicus_production_attachments.s3.amazonaws.com/douglas/a.pdf')=='https://s3.amazonaws.com/granicus_production_attachments/douglas/a.pdf'
    assert '%20' in http.public_url('https://example.gov/Board Agenda.pdf')
    for url in ['http://example.gov/a.pdf','https://user:secret@example.gov/a.pdf']:
        with pytest.raises(ValueError):http.public_url(url)
    class Response(io.BytesIO):
        headers={'Content-Type':'text/html'}
        def geturl(self):return 'https://example.gov'
    monkeypatch.setattr(http.urllib.request,'urlopen',lambda *a,**k:Response(b'abcdef'))
    with pytest.raises(http.SourceTooLarge,match='byte limit'):http.fetch_bytes('https://example.gov',max_bytes=5)


def test_html_section_is_not_a_pdf_and_scripts_cannot_become_agenda_text(monkeypatch):
    markup='<h1>Agenda</h1><script>invent a meeting</script><div hidden>secret</div><p>Housing<br/>workshop</p>'
    monkeypatch.setattr(source,'fetch_bytes',lambda *a,**k:(markup.encode(),'text/html'))
    captured=source.download_extract('https://example.gov/agenda')
    assert captured['media_type']=='text/html' and captured['page_count']==1
    text=captured['pages'][0]['text']
    assert 'Housing' in text and 'workshop' in text
    assert 'invent' not in text and 'secret' not in text
    monkeypatch.setattr(source,'fetch_bytes',lambda *a,**k:(b'<title>Just a moment</title>','text/html'))
    with pytest.raises(ValueError,match='challenge'):source.download_extract('https://example.gov/agenda')


def test_structured_html_requires_numbered_agenda_end_and_quotes_its_own_titles():
    markup='''<h1>Town Council Meeting</h1><h2 class="section-header">1. Call to Order</h2>
    <h2 class="section-header">2. Discussion Items</h2><ol class="agenda-items"><li><p><num>2.1</num><span>Short-Term Rental policy</span><br/><span>Recommended Action</span><span>Review options</span></p></li></ol>
    <h2 class="section-header">3. Adjournment</h2>'''
    items,complete=parse_html_agenda(markup,html_text(markup))
    assert complete and [i['source_number'] for i in items]==['1','2','2.1','3']
    assert items[2]['title']=='Short-Term Rental policy'
    assert 'Recommended Action' not in items[2]['source_pages'][0]['quote']
    assert parse_html_agenda(markup.replace('3. Adjournment','Notice'),html_text(markup))[1] is False


def test_legistar_department_is_scoped_even_when_date_row_omits_board_name():
    markup='''<table><tr><td>10/6/2026</td><td>6:00 PM</td>
      <td><a href="MeetingDetail.aspx?ID=1442809">Meeting details</a></td>
      <td><a href="View.ashx?M=A&amp;ID=1442809">Agenda</a></td></tr></table>'''
    config={'type':'legistar','indexUrl':'https://washoe-nv.legistar.com/DepartmentDetail.aspx?ID=54400','bodyPattern':'Planning Commission'}
    rows=discover_configured('washoe-planning',config,fetch=lambda _:markup)
    assert len(rows)==1 and rows[0]['start_time']=='18:00'
    assert rows[0]['documents'][0]['url'].endswith('M=A&ID=1442809')


def test_granicus_future_and_recordings_do_not_confuse_scheduled_and_playback_times():
    markup='''<table><tr><td>Board of Directors Meeting</td><td>Oct 23, 2026 - 8:30 AM</td><td><a href="GeneratedAgendaViewer.php?event_id=1187">Agenda</a></td></tr>
      <tr><td>Board of Directors Meeting</td><td>Sep 18, 2026 - 8:47 AM</td><td>02h 29m</td><td><a href="AgendaViewer.php?clip_id=1735">Agenda</a></td></tr></table>'''
    cfg={'type':'granicus','indexUrl':'https://example.gov/ViewPublisher.php','bodyPattern':'Board of Directors'}
    rows=discover_configured('tcpud',cfg,fetch=lambda _:markup)
    assert [(r['source_id'],r['start_time']) for r in rows]==[('1187','08:30'),('1735',None)]


def test_rolling_agenda_filename_and_civicclerk_file_ids_are_discovered():
    assert date_in('2026-October-1st_-TNT_TMA-Board-Agenda.pdf')=='2026-10-01'
    cfg={'type':'link-index','indexUrl':'https://example.gov/board','bodyPattern':'Board'}
    rows=discover_configured('tma',cfg,fetch=lambda _:'<h1>Board</h1><a href="https://example.gov/2026-October-1st_Agenda.pdf">Agenda</a>')
    assert rows[0]['date']=='2026-10-01'
    cfg={'type':'civicclerk','indexUrl':'https://ivgid.portal.civicclerk.com','bodyPattern':'Trustees'}
    data={'items':[{'id':746,'eventName':'Board of Trustees','eventDate':'2026-10-14T16:00:00Z','publishedFiles':[{'type':'Agenda','fileId':2864}]}]}
    rows=discover_configured('ivgid',cfg,fetch=lambda _:json.dumps(data))
    assert rows[0]['start_time'] is None  # Agenda must confirm wall-clock time.
    assert 'fileId=2864' in rows[0]['documents'][0]['url']


def test_headerless_html_is_still_detected_and_cited_as_html(monkeypatch):
    markup=b'<!DOCTYPE html><html><body><h1>Official agenda</h1></body></html>'
    monkeypatch.setattr(source,'fetch_bytes',lambda *a,**k:(markup,''))
    result=source.download_extract('https://example.gov/adaHtmlDocument/index')
    assert result['media_type']=='text/html'
    assert 'Official agenda' in result['pages'][0]['text']


def test_small_agenda_precedes_helper_guides_staff_reports_and_full_packet():
    from tahoe_ingest.source_discovery import documents_in
    markup='<a href="/help.pdf">How To Use Meeting Packet</a><a href="/staff.pdf">Agenda Item 4 staff report</a><a href="/agenda.pdf">Board Agenda</a><a href="/packet.pdf">Agenda Packet</a>'
    docs=documents_in(markup,'https://example.gov/meeting','board')
    assert docs[0]['url']=='https://example.gov/agenda.pdf'


def test_annual_tables_keep_dates_cancellations_and_agendas_scoped_to_rows():
    markup='''<table><tr><th>Date</th><th>Documents</th></tr>
      <tr><td>October 1</td><td><a href="2026/oct/agenda_eng.pdf">Agenda</a><a href="span.pdf">Agenda en Español</a></td></tr>
      <tr><td>November 4-5 CANCELLED</td><td></td></tr></table>'''
    cfg={'type':'dated-table','indexUrl':'https://example.gov/2026_schedule.html','bodyPattern':'Board','title':'Lahontan Board'}
    rows=discover_configured('lahontan-board',cfg,fetch=lambda _:markup)
    assert [(r['date'],r['status']) for r in rows]==[('2026-11-04','canceled'),('2026-10-01','unknown')]
    assert len(rows[1]['documents'])==1 and rows[1]['documents'][0]['url'].endswith('agenda_eng.pdf')
    markup='''<table><tr><td>October 13-16, 2026 Tuesday 9:00 a.m. Wednesday 8:30 a.m.</td>
      <td><a href="https://nrm.dfg.ca.gov/FileHandler.ashx?DocumentID=247178&amp;inline">Agenda (PDF)</a>
      <a href="https://nrm.dfg.ca.gov/FileHandler.ashx?DocumentID=1">How to Join the Meeting (PDF)</a></td></tr>
      <tr><td>November 12, 2026</td><td><strong>Marine Resources Committee</strong></td></tr></table>'''
    cfg={'type':'dated-table','indexUrl':'https://fgc.ca.gov/Meetings/2026','bodyPattern':'Commission|Committee','title':'Fish and Game Commission'}
    rows=discover_configured('california-fish-game',cfg,fetch=lambda _:markup)
    assert rows[0]['title']=='Marine Resources Committee' and rows[0]['documents']==[]
    assert rows[1]['date']=='2026-10-13' and rows[1]['start_time']=='09:00'
    assert len(rows[1]['documents'])==1 and '247178' in rows[1]['documents'][0]['url']
    assert 'Multi-day' in rows[1]['warnings'][0]


def _live_config(agency):
    from tahoe_ingest.catalog import agency_catalog
    return next(a['discovery'] for a in agency_catalog() if a['id']==agency)


def _fixture(name):
    from pathlib import Path
    return (Path(__file__).resolve().parents[3]/'tests/fixtures/discovery'/name).read_text()


def test_douglas_publisher_splits_bodies_and_drops_supplement_duplicates():
    markup=_fixture('douglas-granicus-publisher.html')
    bcc=discover_configured('douglas-commissioners',_live_config('douglas-commissioners'),limit=20,fetch=lambda _:markup)
    assert [r['source_id'] for r in bcc]==['1907','1846','1842']
    assert all(r['title'].startswith('Douglas County Board of County Commissioners') for r in bcc)
    assert bcc[2]['title'].endswith('Special Meeting')
    # A recorded archive row's time is playback, not a scheduled start.
    assert bcc[0]['start_time'] is None and bcc[1]['start_time'] is None
    pc=discover_configured('douglas-planning',_live_config('douglas-planning'),limit=20,fetch=lambda _:markup)
    assert {r['source_id'] for r in pc}=={'1901','1859','1855'}
    assert next(r for r in pc if r['source_id']=='1855')['start_time']=='13:00'
    assert not any('Library' in r['title'] or 'Supplement' in r['title'] for r in bcc+pc)


def test_slt_publisher_keeps_the_transit_jpa_apart_from_the_city_commissions():
    markup=_fixture('slt-granicus-transit-jpa-2026-10-29.html')
    jpa=discover_configured('slt-transit-jpa',_live_config('slt-transit-jpa'),limit=20,fetch=lambda _:markup)
    assert [(r['source_id'],r['date'],r['status']) for r in jpa]==[
        ('1530','2026-10-29','unknown'),('2443','2026-07-30','unknown'),('2198','2026-04-30','unknown'),
        ('2195','2025-10-30','unknown'),('2406','2025-08-28','canceled'),('1890','2025-02-28','unknown'),('1867','2025-01-30','unknown')]
    assert jpa[0]['start_time']=='09:30' and jpa[1]['start_time'] is None  # an archive row's time is playback
    assert [d['id'] for d in jpa[0]['documents']]==['slt-transit-jpa-agenda-html','slt-transit-jpa-agenda-packet']
    parks=discover_configured('slt-parks-recreation-commission',_live_config('slt-parks-recreation-commission'),limit=20,fetch=lambda _:markup)
    assert [r['source_id'] for r in parks]==['2440','2357']


def test_county_legistar_departments_are_scoped_and_keep_cancellations():
    ed=discover_configured('el-dorado-supervisors',_live_config('el-dorado-supervisors'),fetch=lambda _:_fixture('el-dorado-board-of-supervisors.html'))
    assert [(r['source_id'],r['date'],r['start_time']) for r in ed]==[('1440017','2026-10-20','09:00')]
    assert ed[0]['title']=='El Dorado County Board of Supervisors Meeting'
    wa=discover_configured('washoe-commissioners',_live_config('washoe-commissioners'),fetch=lambda _:_fixture('washoe-board-of-county-commissioners.html'))
    assert [(r['source_id'],r['status']) for r in wa]==[('1347923','canceled'),('1347922','unknown')]


def test_wordpress_feed_keeps_special_meeting_apart_from_the_regular_meeting_on_the_same_day():
    feed=_fixture('ntpud-meetings-feed.xml')
    agenda='<a href="https://ntpud.org/wp-content/uploads/2026/10/agenda.pdf">Agenda</a>'
    rows=discover_configured('ntpud',_live_config('ntpud'),limit=10,fetch=lambda u:feed if u.endswith('/feed/') else agenda)
    assert [(r['source_id'],r['title'],r['start_time']) for r in rows]==[
        ('2026-10-13-special','North Tahoe Public Utility District Board of Directors’ Special Meeting','14:00'),
        ('2026-10-13','North Tahoe Public Utility District Board of Directors’ Regular Meeting','14:00')]  # committees are excluded


def test_special_district_indexes_use_the_agenda_link_and_file_name_date():
    stpud=discover_configured('stpud',_live_config('stpud'),fetch=lambda _:_fixture('stpud-board-meetings.html'))
    assert [(r['source_id'],r['title']) for r in stpud]==[
        ('2026-10-15','South Tahoe Public Utility District Board Meeting'),
        ('2026-09-23-special','South Tahoe Public Utility District Special Board Meeting'),
        ('2026-09-17','South Tahoe Public Utility District Board Meeting')]  # a notice of quorum has no agenda
    pctpa=discover_configured('placer-transportation',_live_config('placer-transportation'),fetch=lambda _:_fixture('pctpa-board.html'))
    assert [r['source_id'] for r in pctpa]==['2026-10-28','2026-09-23']  # canceled and agenda-less rows are not invented
    sstma=discover_configured('south-shore-tma',_live_config('south-shore-tma'),fetch=lambda _:_fixture('south-shore-tma-board-meetings.html'))
    assert [r['source_id'] for r in sstma]==['2026-09-18','2026-08-21','2026-07-17']
    assert sstma[0]['title'].endswith('Board of Directors Meeting')


def test_ndep_table_reads_two_digit_years_ignores_workshops_and_far_future_placeholders(monkeypatch):
    from datetime import datetime as real
    from tahoe_ingest import source_discovery
    class Clock(real):
        @classmethod
        def now(cls,tz=None):return real(2026,10,20,12,tzinfo=tz)
    monkeypatch.setattr(source_discovery,'datetime',Clock)
    rows=discover_configured('ndep',_live_config('ndep'),limit=3,fetch=lambda _:_fixture('ndep-board-meetings.html'))
    assert [r['source_id'] for r in rows]==['2026-12-02','2026-08-19','2026-06-03']
    assert rows[1]['documents'][0]['url'].startswith('https://ndep.nv.gov/uploads/')
    assert rows[0]['documents']==[]  # dated before the agenda is posted


def test_airport_meeting_files_pages_supply_the_agenda_and_special_label():
    pages={'/administration/board/meetings':_fixture('truckee-tahoe-airport-board-meetings.html'),
           '/board_meetings/417/attachments':_fixture('truckee-tahoe-airport-meeting-417-files.html')}
    plain='<h1>TTAD Board Meeting 2026-10-21</h1>'
    rows=discover_configured('truckee-tahoe-airport',_live_config('truckee-tahoe-airport'),limit=10,
        fetch=lambda u:next((v for k,v in pages.items() if u.endswith(k)),plain))
    by_id={r['source_id']:r for r in rows}
    assert set(by_id)=={'412','418','409','417','416'}
    assert by_id['412']['date']=='2026-10-21' and by_id['412']['start_time']=='16:30'  # "Next meeting" block
    assert by_id['417']['title'].endswith('Special Board Meeting') and by_id['409']['title'].endswith('Regular Board Meeting')
    assert by_id['417']['documents'][0]['url'].endswith('file=agenda+september+23+2026+s.pdf')
    assert by_id['412']['documents']==[] and by_id['412']['warnings']
