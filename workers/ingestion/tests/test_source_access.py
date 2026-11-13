import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import urllib.error

import pytest

from tahoe_ingest import http, source
from tahoe_ingest.html_source import html_text
from tahoe_ingest.html_agenda import parse_html_agenda
from tahoe_ingest.source_discovery import discover_configured, date_in


class Body(io.BytesIO):
    """A urlopen response without Content-Length, counting reads."""
    def __init__(self,data,content_type='application/pdf',url='https://example.gov/a.pdf'):
        super().__init__(data);self.headers={'Content-Type':content_type};self.url=url;self.reads=0
    def geturl(self):return self.url
    def read(self,*a):
        self.reads+=1;return super().read(*a)


def serve(monkeypatch,data,content_type):
    monkeypatch.setattr(http.urllib.request,'urlopen',lambda *a,**k:Body(data,content_type))


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


def test_declared_content_length_over_the_limit_is_rejected_before_reading(monkeypatch):
    class Response(io.BytesIO):
        headers={'Content-Type':'application/pdf','Content-Length':'428948660'}
        def geturl(self):return 'https://example.gov/packet.pdf'
        def read(self,*a):raise AssertionError('the body must not be read')
    monkeypatch.setattr(http.urllib.request,'urlopen',lambda *a,**k:Response())
    with pytest.raises(http.SourceTooLarge,match='428948660 bytes declared'):http.fetch_bytes('https://example.gov/packet.pdf',max_bytes=100)
    class Small(io.BytesIO):
        headers={'Content-Type':'application/pdf','Content-Length':'9'}
        def geturl(self):return 'https://example.gov/a.pdf'
    monkeypatch.setattr(http.urllib.request,'urlopen',lambda *a,**k:Small(b'%PDF-test'))
    assert http.fetch_bytes('https://example.gov/a.pdf',max_bytes=100)[0]==b'%PDF-test'
    assert issubclass(source.TooManyPages,http.SourceTooLarge) and issubclass(source.TooManyPages,ValueError)


def test_html_section_is_not_a_pdf_and_scripts_cannot_become_agenda_text(monkeypatch):
    markup='<h1>Agenda</h1><script>invent a meeting</script><div hidden>secret</div><p>Housing<br/>workshop</p>'
    serve(monkeypatch,markup.encode(),'text/html')
    captured=source.download_extract('https://example.gov/agenda')
    assert captured['media_type']=='text/html' and captured['page_count']==1
    text=captured['pages'][0]['text']
    assert 'Housing' in text and 'workshop' in text
    assert 'invent' not in text and 'secret' not in text
    serve(monkeypatch,b'<title>Just a moment</title>','text/html')
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
    assert rows[1]['warnings']==[]  # the start comes from storage or the agenda; nothing for a resident to check


def test_rolling_agenda_filename_and_civicclerk_file_ids_are_discovered():
    assert date_in('2026-October-1st_-TNT_TMA-Board-Agenda.pdf')=='2026-10-01'
    cfg={'type':'link-index','indexUrl':'https://example.gov/board','bodyPattern':'Board'}
    rows=discover_configured('tma',cfg,fetch=lambda _:'<h1>Board</h1><a href="https://example.gov/2026-October-1st_Agenda.pdf">Agenda</a>')
    assert rows[0]['date']=='2026-10-01'
    cfg={'type':'civicclerk','indexUrl':'https://ivgid.portal.civicclerk.com','bodyPattern':'Trustees'}
    data={'items':[{'id':746,'eventName':'Board of Trustees','eventDate':'2026-10-14T16:00:00Z','publishedFiles':[{'type':'Agenda','fileId':2864}]}]}
    rows=discover_configured('ivgid',cfg,fetch=lambda _:json.dumps(data))
    # The "Z" is not an offset: 16:00 is the 4:00 p.m. local start the agenda prints (decision 0038).
    assert rows[0]['start_time']=='16:00' and rows[0]['warnings']==[]
    assert 'fileId=2864' in rows[0]['documents'][0]['url']
    data['items'][0]['eventDate']='2026-10-14T00:00:00Z'  # midnight: no time entered
    assert discover_configured('ivgid',cfg,fetch=lambda _:json.dumps(data))[0]['start_time'] is None


def test_headerless_html_is_still_detected_and_cited_as_html(monkeypatch):
    markup=b'<!DOCTYPE html><html><body><h1>Official agenda</h1></body></html>'
    serve(monkeypatch,markup,'')
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
    assert rows[0]['warnings']==[]  # a canceled meeting's span is not worth a note
    markup='''<table><tr><td>October 13-16, 2026 Tuesday 9:00 a.m. Wednesday 8:30 a.m.</td>
      <td><a href="https://nrm.dfg.ca.gov/FileHandler.ashx?DocumentID=247178&amp;inline">Agenda (PDF)</a>
      <a href="https://nrm.dfg.ca.gov/FileHandler.ashx?DocumentID=1">How to Join the Meeting (PDF)</a></td></tr>
      <tr><td>November 12, 2026</td><td><strong>Marine Resources Committee</strong></td></tr></table>'''
    cfg={'type':'dated-table','indexUrl':'https://fgc.ca.gov/Meetings/2026','bodyPattern':'Commission|Committee','title':'Fish and Game Commission'}
    rows=discover_configured('california-fish-game',cfg,fetch=lambda _:markup)
    assert rows[0]['title']=='Marine Resources Committee' and rows[0]['documents']==[]
    assert rows[1]['date']=='2026-10-13' and rows[1]['start_time']=='09:00'
    assert len(rows[1]['documents'])==1 and '247178' in rows[1]['documents'][0]['url']
    assert rows[1]['warnings']==['Meets October 13–16, 2026.'] and rows[0]['warnings']==[]


def test_multi_day_note_states_the_span():
    from tahoe_ingest.source_discovery import multi_day_note
    def note(day,last,status='unknown'):
        row={'date':day,'status':status,'warnings':[]}
        multi_day_note(row,last)
        return row['warnings']
    assert note('2026-10-13','14')==['Meets October 13–14, 2026.']
    assert note('2026-09-30','1')==['Meets September 30 – October 1, 2026.']
    assert note('2026-12-31','1')==['Meets December 31, 2026 – January 1, 2027.']
    assert note('2026-10-13','14','canceled')==[]
    assert note('2026-02-27','31')==[] and note('2026-10-13','13')==[]  # no such day; not a range


def test_yearly_index_reads_the_neighbouring_year_inside_the_window(monkeypatch):
    from datetime import date
    from tahoe_ingest import source_discovery
    pages={'https://fgc.ca.gov/Meetings/2026':'''<table>
        <tr><td>October 13-14, 2026 Tuesday, 9:00 a.m.</td><td></td></tr>
        <tr><td>December 15-16, 2026 Tuesday and Wednesday</td><td></td></tr></table>''',
           'https://fgc.ca.gov/Meetings/2027':'''<table>
        <tr><td>January 13, 2027 Wednesday</td><td></td></tr>
        <tr><td>February 10-11, 2027 Wednesday and Thursday</td><td></td></tr>
        <tr><td>June 9-10, 2027 Wednesday and Thursday</td><td></td></tr></table>'''}
    requested=[]
    def fetch(url):
        requested.append(url)
        if url not in pages:raise OSError('HTTP Error 404: Not Found')
        return pages[url]
    cfg={'type':'dated-table','indexUrl':'https://fgc.ca.gov/Meetings/2026','indexUrlTemplate':'https://fgc.ca.gov/Meetings/{year}',
         'bodyPattern':'Commission','title':'Fish and Game Commission'}
    monkeypatch.setattr(source_discovery,'_local_today',lambda:date(2026,11,9))
    rows=discover_configured('california-fish-game',cfg,limit=20,fetch=fetch)
    assert requested==['https://fgc.ca.gov/Meetings/2026','https://fgc.ca.gov/Meetings/2027']
    # Next year's rows up to the 120-day horizon (March 9) only.
    assert [r['date'] for r in rows]==['2027-02-10','2027-01-13','2026-12-15','2026-10-13']
    assert rows[0]['warnings']==['Meets February 10–11, 2027.']
    requested.clear()
    monkeypatch.setattr(source_discovery,'_local_today',lambda:date(2026,6,1))
    assert len(discover_configured('california-fish-game',cfg,limit=20,fetch=fetch))==2
    assert requested==['https://fgc.ca.gov/Meetings/2026']
    # In January, December's meetings come from last year's page; next year's missing page is skipped.
    requested.clear()
    monkeypatch.setattr(source_discovery,'_local_today',lambda:date(2027,1,20))
    rows=discover_configured('california-fish-game',cfg,limit=20,fetch=fetch)
    assert requested==['https://fgc.ca.gov/Meetings/2027','https://fgc.ca.gov/Meetings/2026']
    assert [r['date'] for r in rows]==['2027-06-09','2027-02-10','2027-01-13','2026-12-15']
    from tahoe_ingest import scheduled
    assert (source_discovery.YEAR_PAGE_PAST_DAYS,source_discovery.YEAR_PAGE_FUTURE_DAYS)==(scheduled.PAST_DAYS,scheduled.FUTURE_DAYS)


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


def test_legistar_location_keeps_the_place_and_drops_links_ids_and_notes():
    from tahoe_ingest.source_discovery import legistar_location
    # EventLocation values the Washoe and El Dorado web APIs returned on 2026-11-03.
    assert legistar_location('1001 East Ninth Street, Building A Lobby, Washoe County Chambers, Reno, Nevada 89512'.splitlines())==\
        '1001 East Ninth Street, Building A Lobby, Washoe County Chambers, Reno, Nevada 89512'
    assert legistar_location('Downtown Reno Library\r\n301 S. Center St\r\nReno, NV 89501'.splitlines())=='Downtown Reno Library, 301 S. Center St, Reno, NV 89501'
    for remote in ('https://edcgov-us.zoom.us/j/89741780356','Meeting ID: 841 9698 5750\r\nhttps://edcgov-us.zoom.us/j/84196985750',
                   'Virtual: Zoom','CANCELLED MEETING NOTICE','',None):
        assert legistar_location((remote or '').splitlines()) is None
    assert legistar_location('Meeting Locations (in person and connected via tele/video-conferencing):\r\n• 330 Fair Lane, Bldg. A, Placerville, CA\r\n'
                             '• Mental Health Office, 1900 Lake Tahoe Blvd., Suite 103, South Lake Tahoe, CA'.splitlines())==\
        '330 Fair Lane, Bldg. A, Placerville, CA; Mental Health Office, 1900 Lake Tahoe Blvd., Suite 103, South Lake Tahoe, CA'


def test_legistar_department_rows_read_the_location_column():
    # 10/20's cell is "https://edcgov-us.zoom.us/j/...<br><em>330 Fair Lane, Building A Placerville, CA\nOR Live Streamed</em>".
    ed=discover_configured('el-dorado-supervisors',_live_config('el-dorado-supervisors'),fetch=lambda _:_fixture('el-dorado-board-of-supervisors.html'))
    assert ed[0]['location']=='330 Fair Lane, Building A Placerville, CA'
    wa=discover_configured('washoe-commissioners',_live_config('washoe-commissioners'),fetch=lambda _:_fixture('washoe-board-of-county-commissioners.html'))
    assert [r['location'] for r in wa]==[None,None]  # blank, and "CANCELLED"


def test_legistar_department_page_lists_only_this_month_so_the_web_api_adds_upcoming_meetings(monkeypatch):
    # On 2026-11-03 the Washoe department page (default filter "This Month") listed only November
    # meetings, none viewable yet, so discovery found nothing: no meeting, and no minutes or video
    # for October's meetings either.
    from tahoe_ingest import source_discovery
    from datetime import date
    monkeypatch.setattr(source_discovery,'_today',lambda:date(2026,11,3))
    page=_fixture('washoe-board-of-county-commissioners-2026-11-03.html')
    base='https://washoe-nv.legistar.com/MeetingDetail.aspx?LEGID={}&GID=601&G=84249440-C071-40A3-8231-F8E59BEF9FE7'
    def event(eid,day,status,agenda=True,media=None,comment=None):
        return {'EventId':eid,'EventBodyName':'Board of County Commissioners','EventDate':day+'T00:00:00','EventTime':'10:00 AM',
                'EventComment':comment,'EventAgendaStatusName':status,'EventMedia':media,'EventInSiteURL':base.format(eid),
                'EventAgendaFile':f'https://washoe-nv.legistar1.com/washoe-nv/meetings/{day[:4]}/{int(day[5:7])}/{eid}_A_Board_of_County_Commissioners_Agenda.pdf' if agenda else None}
    events=[event(1812,'2026-11-10','Closed',agenda=False),  # not public yet: no agenda file, so not listed
            event(1811,'2026-10-27','Final'),event(1810,'2026-10-20','Final',comment='CANCELLED'),
            event(1809,'2026-10-13','Final',media=5106),
            {**event(2033,'2026-10-06','Final'),'EventBodyName':'Board of Fire Commissioners'}]
    fetch=lambda url:json.dumps(events) if url.startswith('https://webapi.legistar.com/v1/washoe-nv/events') else page
    found=discover_configured('washoe-commissioners',_live_config('washoe-commissioners'),fetch=fetch)
    assert [r for r in found if not r.get('records_only')]==[]  # no November agenda has posted
    [video]=[r for r in found if r.get('records_only')]
    assert (video['date'],video['recording_url'])==('2026-10-13','https://washoe-nv.legistar.com/Video.aspx?Mode=Granicus&ID1=5106&Mode2=Video')
    # Once the 11/10 agenda posts it is listed, from the API, before the page shows the meeting.
    events[0].update(EventAgendaStatusName='Final',EventAgendaFile='https://washoe-nv.legistar1.com/washoe-nv/meetings/2026/11/1812_A.pdf',
                     EventLocation='Washoe County Administration Complex\r\nCommission Chambers, Building A')
    [meeting]=[r for r in discover_configured('washoe-commissioners',_live_config('washoe-commissioners'),fetch=fetch) if not r.get('records_only')]
    assert (meeting['source_id'],meeting['date'],meeting['start_time'],meeting['status'])==('1812','2026-11-10','10:00','unknown')
    assert meeting['title']=='Washoe County Board of County Commissioners Meeting' and meeting['official_url']==base.format(1812)
    assert [d['url'] for d in meeting['documents']]==['https://washoe-nv.legistar1.com/washoe-nv/meetings/2026/11/1812_A.pdf']
    assert meeting['location']=='Washoe County Administration Complex, Commission Chambers, Building A'
    # On 2026-10-21 the page (October only, 10/27 not yet viewable) gave two rows; posted agendas
    # for 10/27 and next month's 11/10 are added from the API, while the page's own rows keep their IDs.
    monkeypatch.setattr(source_discovery,'_today',lambda:date(2026,10,21))
    october=_fixture('washoe-board-of-county-commissioners.html')
    rows=discover_configured('washoe-commissioners',_live_config('washoe-commissioners'),
                             fetch=lambda url:json.dumps(events) if 'webapi' in url else october)
    assert [(r['source_id'],r['date']) for r in rows if not r.get('records_only')]==[
        ('1812','2026-11-10'),('1811','2026-10-27'),('1347923','2026-10-20'),('1347922','2026-10-13')]


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


def test_civicplus_category_keeps_the_board_and_not_its_committees():
    # One "Board Meetings & Agendas" category lists the board, its special meetings and workshops, and two committees.
    rows=discover_configured('tdrpd',_live_config('tdrpd'),limit=None,fetch=lambda _:_fixture('tdrpd-agenda-center-2026-11-13.html'))
    board='Truckee-Donner Recreation and Park District Board of Directors Meeting'
    special='Truckee-Donner Recreation and Park District Special Board of Directors Meeting'
    assert [(r['source_id'],r['title'],r['status']) for r in rows]==[
        ('2026-10-22',board,'unknown'),('2026-09-24',board,'unknown'),('2026-08-27',board,'unknown'),
        ('2026-07-23',board,'unknown'),('2026-06-25',board,'unknown'),('2026-05-28',board,'unknown'),
        ('2026-05-14',board,'unknown'),  # "Board Retreat May 2026": its agenda is a regular meeting notice
        ('2026-04-23',board,'canceled'),  # "April Board Meeting Cancelled"
        ('2026-04-09-special',special,'unknown'),('2026-03-26',board,'unknown'),('2026-02-26',board,'unknown'),
        ('2026-02-18-special',special,'unknown'),  # "Special Meeting Notice - TDRPD Board Workshop"
        ('2026-01-22',board,'unknown'),
        ('2026-01-14-special',special,'unknown')]  # "TDRPD Special Master Plan Workshop"
    # Budget & Finance and Facility Condition Assessment ("FCA") committee rows are left out.
    assert not {'2026-10-20','2026-05-26','2026-04-30','2026-04-21','2026-03-24','2026-02-24','2026-02-20'} & {r['date'] for r in rows}
    # The row's "Agenda" download link repeats the title link's URL; it is one document, not a second meeting.
    assert rows[0]['documents']==[{'id':'tdrpd-agenda-packet','title':'October 2026 Board Packet',
                                   'url':'https://www.tdrpd.org/AgendaCenter/ViewFile/Agenda/_10222026-290'}]
    assert rows[-1]['documents'][0]['id']=='tdrpd-agenda'
    # Without qualifySpecial a workshop keeps the plain date ID, and without a body filter every row qualifies.
    plain={k:v for k,v in _live_config('tdrpd').items() if k not in {'qualifySpecial','bodyPattern'}}
    every=discover_configured('tdrpd',plain,limit=None,fetch=lambda _:_fixture('tdrpd-agenda-center-2026-11-13.html'))
    assert len(every)==21 and '2026-01-14' in {r['source_id'] for r in every}


def test_civicclerk_horizon_stops_the_event_list_before_next_years_placeholders(monkeypatch):
    from datetime import date
    from tahoe_ingest import source_discovery
    monkeypatch.setattr(source_discovery,'_local_today',lambda:date(2026,11,13))
    events=_fixture('tdpud-civicclerk-events-2026-11-13.json')
    asked=[]
    def fetch(url):
        asked.append(url)
        return '<html><body>CivicClerk portal</body></html>' if 'portal' in url else events
    rows=discover_configured('tdpud',_live_config('tdpud'),limit=20,fetch=fetch)
    # Newest first: without toDate the first page would hold only 2027 placeholder meetings.
    assert asked[1]=="https://tdpud.api.civicclerk.com/v1/Events/GetEventsList(fromDate='',toDate='2027-03-13',categoryIds=[],skip=0,sortDescending=true,cursorId=0)"
    by_id={r['source_id']:r for r in rows}
    assert '1215' not in by_id  # the Financing Corporation is another body
    assert len(rows)==14 and by_id['1202']['status']=='canceled'  # "CANCELLED Special Board Meeting"
    assert (by_id['1216']['date'],by_id['1216']['start_time'],by_id['1216']['title'])==('2026-10-14','09:00','Special Board Meeting')
    assert [d['id'] for d in by_id['179']['documents']]==['tdpud-agenda','tdpud-agenda-packet']
    assert by_id['179']['official_url']=='https://tdpud.portal.civicclerk.com/event/179/overview'
    assert by_id['172']['documents']==[]  # a calendared meeting whose agenda is not posted yet is a notice
    # Without horizonDays the list is not bounded (IVGID's configuration).
    asked.clear()
    discover_configured('tdpud',{k:v for k,v in _live_config('tdpud').items() if k!='horizonDays'},limit=20,fetch=fetch)
    assert "toDate=''" in asked[1]


def test_ndep_table_reads_two_digit_years_and_the_second_table_of_abbreviated_dates_but_not_far_future_placeholders(monkeypatch):
    from datetime import datetime as real
    from tahoe_ingest import source_discovery
    class Clock(real):
        @classmethod
        def now(cls,tz=None):return real(2026,10,20,12,tzinfo=tz)
    monkeypatch.setattr(source_discovery,'datetime',Clock)
    rows=discover_configured('ndep',_live_config('ndep'),limit=3,fetch=lambda _:_fixture('ndep-board-meetings.html'))
    assert [r['source_id'] for r in rows]==['2026-12-02','2026-11-04','2026-08-19']  # Nov 3 2027 and May 5 2027 are past the horizon
    assert rows[2]['documents'][0]['url'].startswith('https://ndep.nv.gov/uploads/')
    assert rows[0]['documents']==[]  # dated before the agenda is posted


def test_ndep_workshop_table_is_titled_a_workshop_but_keeps_the_date_id(monkeypatch):
    from datetime import datetime as real
    from tahoe_ingest import source_discovery
    class Clock(real):
        @classmethod
        def now(cls,tz=None):return real(2026,10,20,12,tzinfo=tz)
    monkeypatch.setattr(source_discovery,'datetime',Clock)
    rows=discover_configured('ndep',_live_config('ndep'),limit=None,fetch=lambda _:_fixture('ndep-board-meetings.html'))
    by_id={r['source_id']:r['title'] for r in rows}
    meeting,workshop='Nevada Board for Financing Water Projects Meeting','Nevada Board for Financing Water Projects Workshop'
    # The ID is still the date, so a stored "Meeting" for November 4 is retitled by the next run, not duplicated.
    assert by_id['2026-11-04']==workshop and by_id['2026-05-05']==workshop and by_id['2026-01-07']==workshop
    assert by_id['2026-12-02']==by_id['2026-05-20']==meeting
    assert by_id['2022-12-06']==meeting  # "Archived Board Meetings" is not a workshop heading
    assert '2027-05-05' not in by_id  # still beyond the horizon
    assert [d['url'] for r in rows if r['source_id']=='2026-05-05' for d in r['documents']]==[
        'https://ndep.nv.gov/uploads/water-financing-bwfp-agendas-docs/Priority_List_Workshop_agenda_May_2026.pdf']


def test_section_titles_give_same_day_rows_of_another_section_their_own_id():
    markup='''<h3>Meetings</h3><table><tr><td>January 24, 2024</td><td><a href="a.pdf">Agenda</a></td></tr>
      <tr><td>February 7, 2024</td><td><a href="b.pdf">Agenda</a></td></tr></table>
      <div><p>Workshops</p></div><table><tr><td>January 24, 2024</td><td><a href="w.pdf">Agenda</a></td></tr>
      <tr><td>March 6, 2024</td><td><a href="x.pdf">Agenda</a></td></tr></table>'''
    cfg={'type':'dated-table','indexUrl':'https://example.gov/meetings','title':'Board Meeting',
         'sectionTitles':[{'headingPattern':'workshops?$','title':'Board Workshop','idSuffix':'workshop'}]}
    rows=discover_configured('example',cfg,limit=None,fetch=lambda _:markup)
    assert {r['source_id']:r['title'] for r in rows}=={
        '2024-01-24':'Board Meeting','2024-02-07':'Board Meeting',
        '2024-01-24-workshop':'Board Workshop','2024-03-06':'Board Workshop'}
    plain={k:v for k,v in cfg.items() if k!='sectionTitles'}  # without the option nothing changes
    assert {r['title'] for r in discover_configured('example',plain,limit=None,fetch=lambda _:markup)}=={'Board Meeting'}


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
    assert by_id['412']['documents']==[] and by_id['412']['warnings']==[]  # no agenda yet: state, not a warning


def test_streamed_download_matches_the_in_memory_fetch(monkeypatch,tmp_path):
    data=b'%PDF-'+os.urandom(3*http.CHUNK_BYTES+17)
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    serve(monkeypatch,data,'application/pdf')
    with http.download('https://example.gov/a.pdf',max_bytes=len(data)) as fetched:
        assert fetched.path.parent.parent==tmp_path
        assert fetched.sha256==hashlib.sha256(http.fetch_bytes('https://example.gov/a.pdf',max_bytes=len(data))[0]).hexdigest()
        assert fetched.size==len(data) and fetched.path.read_bytes()==data and fetched.content_type=='application/pdf'
    assert list(tmp_path.iterdir())==[]


def test_streamed_download_stops_at_the_byte_limit_without_content_length(monkeypatch,tmp_path):
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    body=Body(b'x'*(5*http.CHUNK_BYTES))
    monkeypatch.setattr(http.urllib.request,'urlopen',lambda *a,**k:body)
    with pytest.raises(http.SourceTooLarge,match='byte limit'):
        with http.download('https://example.gov/a.pdf',max_bytes=http.CHUNK_BYTES+10):
            raise AssertionError('an oversized body must not be yielded')
    assert body.reads==2  # stopped on the chunk that crossed the limit, not at the end of the body
    assert list(tmp_path.iterdir())==[]


def test_streamed_download_keeps_https_skip_and_native_fallback_checks(monkeypatch,tmp_path):
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    monkeypatch.setattr(http.urllib.request,'urlopen',lambda *a,**k:Body(b'%PDF-x',url='http://example.gov/a.pdf'))
    with pytest.raises(ValueError,match='outside HTTPS'):
        with http.download('https://example.gov/a.pdf',max_bytes=100):pass
    def forbidden(*a,**k):raise urllib.error.HTTPError('https://example.gov',403,'blocked',{},None)
    monkeypatch.setattr(http.urllib.request,'urlopen',forbidden)
    def curl(url,output,max_bytes,accept):
        output.write_bytes(b'%PDF-native');return 'application/pdf'
    monkeypatch.setattr(http,'_native_download',curl)
    with http.download('https://example.gov/a.pdf',max_bytes=100) as fetched:
        assert fetched.sha256==hashlib.sha256(b'%PDF-native').hexdigest() and fetched.size==11
    def rate_limited(*a,**k):raise urllib.error.HTTPError('https://example.gov',429,'slow down',{},None)
    monkeypatch.setattr(http.urllib.request,'urlopen',rate_limited)
    with pytest.raises(urllib.error.HTTPError):
        with http.download('https://example.gov/a.pdf',max_bytes=100):pass
    skip=tmp_path/'config'/'skipped-endpoints.json';skip.parent.mkdir()
    skip.write_text(json.dumps({'skipped':[{'prefix':'https://blocked.gov/','status':403}]}))
    monkeypatch.setenv('TAHOE_SOURCE_ROOT',str(tmp_path))
    with pytest.raises(http.EndpointSkipped):
        with http.download('https://blocked.gov/a.pdf',max_bytes=100):pass
    skip.unlink();skip.parent.rmdir()
    assert list(tmp_path.iterdir())==[]


def test_temp_file_is_removed_when_reading_fails(monkeypatch,tmp_path):
    from tahoe_ingest import staff_reports
    monkeypatch.setattr(tempfile,'tempdir',str(tmp_path))
    serve(monkeypatch,b'%PDF-1.4 not really a pdf','application/pdf')
    with pytest.raises(subprocess.CalledProcessError):source.download_extract('https://example.gov/a.pdf')
    with pytest.raises(subprocess.CalledProcessError):staff_reports.read_pdf('https://example.gov/a.pdf')
    serve(monkeypatch,b'<p>not a pdf</p>','text/html')
    with pytest.raises(staff_reports.Uncertain):staff_reports.read_pdf('https://example.gov/a.pdf')
    with pytest.raises(RuntimeError):
        with http.download('https://example.gov/a.pdf',max_bytes=100):raise RuntimeError('caller failed')
    assert list(tmp_path.iterdir())==[]


def test_wordpress_feed_attaches_only_the_agenda_and_packet_not_other_uploads():
    feed=_fixture("ntpud-meetings-feed.xml")
    page=("<a href=\"https://ntpud.org/wp-content/uploads/2026/09/09092026-Regular-Board-Meeting-Agenda-and-Packet.pdf\">Agenda and Packet</a>"
          "<a href=\"https://ntpud.org/wp-content/uploads/2025/09/2026-Budget-Book-Print-Version.pdf\">FY26 Budget and Capital Plan</a>")
    rows=discover_configured("ntpud",_live_config("ntpud"),limit=10,fetch=lambda u:feed if u.endswith("/feed/") else page)
    assert rows and all([d["url"] for d in r["documents"]]==["https://ntpud.org/wp-content/uploads/2026/09/09092026-Regular-Board-Meeting-Agenda-and-Packet.pdf"] for r in rows)


def test_abbreviated_month_dates_are_read():
    assert date_in("Nov 4, 2026")=="2026-11-04"
    assert date_in("Sept. 17, 2026")=="2026-09-17"
    assert date_in("Sep 17 2026")=="2026-09-17"
    assert date_in("Dec. 2, 2026 workshop")=="2026-12-02"


def test_ndep_index_lists_a_numeric_date_and_an_abbreviated_month_date():
    table=("<table><tr><td>10/21/26</td><td><a href=\"/uploads/a.pdf\">Agenda</a></td></tr>"
           "<tr><td>Nov 4, 2026</td><td><strong>Workshop</strong> <a href=\"/uploads/w.pdf\">Agenda</a></td></tr>"
           "<tr><td>Sept. 17, 2026</td><td></td></tr><tr><td>Dec 2</td><td></td></tr></table>")
    rows=discover_configured("ndep",_live_config("ndep"),limit=10,fetch=lambda _:table)
    assert sorted(r["date"] for r in rows)==["2026-09-17","2026-10-21","2026-11-04"]


def test_a_cancellation_notice_title_names_the_canceled_meeting():
    from tahoe_ingest.source_discovery import candidate
    row=candidate("truckee","8d8df4df-3bbc-452c-8107-c91b6480d648","Notice of Cancelation of Town Council Meeting","2026-10-13","https://example.test/m",[],"5:00 PM")
    assert (row["title"],row["status"])==("Town Council Meeting","canceled")
    assert candidate("x","1","Notice of Cancellation of the Board Meeting","2026-10-13","https://example.test/m",[])["title"]=="Board Meeting"
    assert candidate("x","1","Special Town Council Meeting","2026-10-13","https://example.test/m",[])["status"]=="unknown"


def test_agency_with_a_skipped_source_exports_as_blocked(monkeypatch,tmp_path):
    from tahoe_ingest.core import agency_freshness, AGENCIES
    skip=tmp_path/'config'/'skipped-endpoints.json';skip.parent.mkdir()
    skip.write_text(json.dumps({'skipped':[{'prefix':'https://blocked.gov/','status':403,'since':'2026-10-20','reason':'Every request returns 403.'}]}))
    monkeypatch.setenv('TAHOE_SOURCE_ROOT',str(tmp_path))
    status,error=agency_freshness({'sourceUrl':'https://blocked.gov/agendas'},'ok',None)
    assert status=='blocked' and 'HTTP 403 since 2026-10-20' in error and 'Every request returns 403.' in error
    assert agency_freshness({'sourceUrl':'https://open.gov/','discovery':{'indexUrl':'https://blocked.gov/list'}},'ok',None)[0]=='blocked'
    assert agency_freshness({'sourceUrl':'https://open.gov/'},'error','timeout')==('error','timeout')
    assert agency_freshness({'sourceUrl':'https://open.gov/'},'never_checked',None)==('never_checked',None)
    # The checked-in skip list covers every Placer County page on www.placer.ca.gov, whatever the stored status.
    monkeypatch.setenv('TAHOE_SOURCE_ROOT',str(Path(__file__).resolve().parents[3]))
    for aid in ('placer-supervisors','placer-planning','placer-tahoe-design-review'):
        assert agency_freshness(AGENCIES[aid],'ok',None)[0]=='blocked'
    assert agency_freshness(AGENCIES['placer-transportation'],'ok',None)==('ok',None)


def test_export_check_accepts_blocked_agencies():
    from tahoe_ingest import export_check
    agency={'id':'placer-supervisors','name':'Placer','shortName':'Placer','officialUrl':'https://www.placer.ca.gov/bos',
            'sourceUrl':'https://www.placer.ca.gov/10616/_2026','lastCheckedAt':None,'error':'skipped'}
    base={'schemaVersion':2,'mode':'live','coverageNote':'x','generatedAt':'2026-11-02T00:00:00Z','meetings':[]}
    for status,ok in (('blocked',True),('stale',False)):
        errors,_=export_check.check({**base,'agencies':[{**agency,'status':status}]})
        assert (not any('freshness status' in e for e in errors))==ok


def test_each_agency_exports_its_own_coverage_note(monkeypatch):
    from tahoe_ingest import export_check
    from tahoe_ingest.core import public_agency
    monkeypatch.setenv('TAHOE_SOURCE_ROOT',str(Path(__file__).resolve().parents[3]))
    def row(aid):
        return {'id':aid,'name':aid,'short_name':aid,'official_url':'https://example.gov/','source_url':None,
                'last_checked_at':None,'status':'ok','error':None}
    ttd,advisory=public_agency(row('ttd'),'https://example.gov/m'),public_agency(row('trpa-advisory'),'https://example.gov/m')
    assert ttd['coverageNote']=='Meeting recordings are not published; TTD shares them on request.'
    assert 'coverageNote' not in advisory and ttd['sourceUrl']=='https://example.gov/m'
    base={'schemaVersion':2,'mode':'live','coverageNote':'x','generatedAt':'2026-11-02T00:00:00Z','meetings':[]}
    for note,ok in ((ttd['coverageNote'],True),('  ',False),(None,False)):
        errors,_=export_check.check({**base,'agencies':[{**ttd,'coverageNote':note}]})
        assert (not any('coverageNote' in e for e in errors))==ok
