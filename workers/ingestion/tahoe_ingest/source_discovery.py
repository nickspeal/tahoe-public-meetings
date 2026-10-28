"""Live discovery: each agency's `discovery` block names a source type and how to read it.

`discover_configured` (scheduled runs) and `discover_configured_range` (backfill)
dispatch on the type; `validate_discovery` checks a block against its type's keys.
URLs and body filters are operator configuration, never instructions from HTML.
Dates must be written on a source row or document filename, not inferred from
regular meeting patterns. Agenda-only documents are preferred over full packets.
Minutes and recording links on a listing are kept as candidate fields
(tahoe_ingest.records); a link for an earlier meeting becomes a records-only entry.
"""
from __future__ import annotations
import html
import hashlib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .http import fetch_bytes
from .discovery import (anchors, fetch_text, _pdf_links, granicus_row_ids, granicus_row_ids_range,
                        wordpress_meetings, wordpress_meetings_range)
from .html_dom import document
from .records import granicus_links, listing_minutes, qualifiers, recording_link, records_entry

# Each discovery type and the keys it reads besides `type`, `indexUrl` (always https), and
# `bodyPattern`; config/README.md describes them. Index pages fetched by the shared reader
# below also take `indexUrlTemplate` and `horizonDays`.
_PAGE = {'indexUrlTemplate','horizonDays'}
TYPES={'civicplus':{'title','idPrefix'}|_PAGE,
       'link-index':{'title','idPrefix','qualifySpecial','minutesLinks'}|_PAGE,
       'trpa':{'idPrefix','documentPattern'}|_PAGE,
       'dated-table':{'title','datePattern'}|_PAGE,
       'legistar':{'title','legistarClient'}|_PAGE,
       'granicus':{'title','titlePrefix'}|_PAGE,
       'meeting-files':{'title','filesLabel'}|_PAGE,
       'rss':{'titlePrefix'}|_PAGE,
       'civicclerk':{'apiUrl','portalUrl'},
       'municode':set(),
       'granicus-row-ids':set(),
       'wordpress-meetings':{'documentIdPrefix'}}
# Rows of these types state no meeting date, so a scheduled run cannot window them before
# reading, and each costs a page fetch: it bounds them by count instead.
UNDATED_TYPES=frozenset({'wordpress-meetings'})


def validate_discovery(agency):
    """Check an agency entry's `discovery` block against its type. An entry without one has no live discovery."""
    aid,config=agency['id'],agency.get('discovery')
    if 'discoveryAdapter' in agency:
        raise ValueError(f'{aid}: discoveryAdapter is retired; the discovery block\'s type selects the reader')
    if config is None:return
    keys=TYPES.get(config.get('type') if isinstance(config,dict) else None)
    if keys is None or not str(config.get('indexUrl','')).startswith('https://'):
        raise ValueError(f'invalid source configuration: {aid}')
    if unknown:=set(config)-keys-{'type','indexUrl','bodyPattern'}:
        raise ValueError(f'invalid source configuration: {aid}: {config["type"]} does not read {", ".join(sorted(unknown))}')
    for key in ('bodyPattern','documentPattern','datePattern','filesLabel'):
        re.compile(config.get(key,'.'),re.I)
    if config['type']=='municode' and not _municode_client(config):
        raise ValueError(f'invalid source configuration: {aid}: a Municode indexUrl names its clientCode')


def date_in(text):
    text = text.replace('_', ' ').replace('+', ' ')
    text = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', text, flags=re.I)
    for pattern, fmt in [(r'\b\d{4}-\d{2}-\d{2}(?!\d)','%Y-%m-%d'),
                         (r'\b\d{1,2}/\d{1,2}/\d{4}\b','%m/%d/%Y'),
                         (r'\b\d{4}\.\d{2}\.\d{2}\b','%Y.%m.%d'),
                         (r'\b\d{1,2}\.\d{1,2}\.\d{2}\b','%m.%d.%y'),
                         (r'\b\d{4}-[A-Za-z]+-\d{1,2}\b','%Y-%B-%d'),
                         (r'\b[A-Za-z]{3} \d{1,2},? \d{4}\b','%b %d %Y'),
                         (r'\b\d{1,2}-\d{1,2}-\d{4}\b','%m-%d-%Y'),
                         (r'(?<!\d)\d{8}(?!\d)','%m%d%Y'),
                         (r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b','%B %d %Y')]:
        for found in re.finditer(pattern,text,re.I):
            try:return datetime.strptime(found.group().replace(',',''),fmt).date().isoformat()
            except ValueError:continue
    return None


def time_in(text):
    found=re.search(r'\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s*m\.?\b',text,re.I)
    if not found:return None
    h=int(found[1])%12+(12 if found[3].lower()=='p' else 0)
    return f'{h:02}:{int(found[2] or 0):02}'


def documents_in(markup, base, agency):
    docs=_pdf_links(markup,base,agency)
    existing={d['url'] for d in docs}
    for url,label in anchors(markup,base):
        parsed=urllib.parse.urlparse(url)
        query=urllib.parse.parse_qs(parsed.query)
        lower=(label+' '+parsed.path).lower()
        is_pdf=bool(re.search(r'/(?:AgendaCenter/ViewFile/Agenda|DocumentCenter/View)/',parsed.path,re.I) or
                    (parsed.path.lower().endswith('/filehandler.ashx') and query.get('DocumentID')) or
                    (parsed.path.lower().endswith('/view.ashx') and query.get('M',[''])[0] in {'A','PA'}))
        is_html=bool('adaHtmlDocument' in parsed.path or 'AgendaViewer.php' in parsed.path or
                     'GeneratedAgendaViewer.php' in parsed.path or
                     (parsed.path.lower().endswith('/view.ashx') and query.get('M',[''])[0]=='AADA'))
        if parsed.scheme!='https' or url in existing or not (is_pdf or is_html):continue
        role='agenda-packet' if 'packet' in lower or query.get('M')==['PA'] else 'agenda'
        suffix='-html' if is_html else ''
        docs.append({'id':agency+'-'+role+suffix,'title':label or ('Official agenda (HTML)' if is_html else 'Official agenda'), 'url':url})
        existing.add(url)
    docs=agenda_then_packet(docs)
    counts={i:sum(d['id']==i for d in docs) for i in {d['id'] for d in docs}}
    for d in docs:
        if counts[d['id']]>1:d['id']+='-'+hashlib.sha256(d['url'].encode()).hexdigest()[:10]
    return docs


def _document_rank(d):
    text=(d['title']+' '+d['id']).lower()
    extra=bool(re.search(r'how to|help guide|staff report|agenda item|public comment',d['title'],re.I)) or 'attachment' in d['id']
    return (extra,'packet' in text,'minutes' in text,not bool(re.search(r'html|AADA',d['title']+' '+d['url'],re.I)))


def agenda_then_packet(docs):
    """Order a meeting's documents: the agenda, then its full packet, then minutes and other material.

    Capture keeps only the first few (`max_documents`, 2 by default), so when a page
    offers both a short agenda and a packet, the packet comes second, ahead of a
    second copy of the agenda (an HTML agenda's PDF), other agendas, and minutes.
    Readers then find both the agenda and the packet under the meeting's sources.
    """
    docs=sorted(docs,key=_document_rank)  # accessible HTML / short agenda first, then packets, then minutes
    first=_document_rank(docs[0]) if docs else None
    if first and not any(first[:3]):
        packet=next((i for i,d in enumerate(docs) if i>1 and _document_rank(d)[:2]==(False,True)),None)
        if packet is not None:
            docs.insert(1,docs.pop(packet))
    return docs


# A TRPA meeting page also links committee agendas, staff reports, attachments,
# minutes, presentations, and public comments; none of them is the body's agenda.
TRPA_NOT_MEETING_DOCUMENT=re.compile(r'\b(?:committees?|items?|minutes|staff reports?|attachments?|exhibits?|comments?|'
                                     r'presentations?|summary|how to|navigation|resolutions?|letters?)\b',re.I)
MONTH_DAY=re.compile(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})\b',re.I)


def trpa_documents(agency, config, markup, base, day):
    """The body's own agenda and then its full packet from a TRPA meeting page.

    The anchor text, or else the PDF's file name, names the body and the document:
    "October 28 Governing Board Agenda", "DRAFT July 8, 2026 APC Meeting Packet"
    (`documentPattern`, else `bodyPattern`, names the body). Committee agendas, staff
    reports, attachments, minutes, and public comments never qualify (minutes links
    come from `page_links`). A stated month and day must be the meeting's.
    IDs are `<agency>-agenda-<date>` and `<agency>-agenda-packet-<date>`, the IDs the
    reviewed meeting files use, so a capture updates those documents instead of
    adding copies. Returns the documents and whether they are the meeting's complete
    set: only when the page offers an agenda or packet, so a page that lists neither
    never removes stored documents.
    """
    named=re.compile(config.get('documentPattern') or config.get('bodyPattern','.'),re.I)
    wanted=re.compile(r'(?:%s)\W+(?:Meeting\W+)?(?P<kind>Agenda|Packet)\b'%named.pattern,re.I)
    found={}
    when=date.fromisoformat(day)
    for url,label in anchors(markup,base):
        parsed=urllib.parse.urlparse(url)
        if parsed.scheme!='https' or not parsed.path.lower().endswith('.pdf'):continue
        label=' '.join(label.split())
        name=re.sub(r'[-_+]+',' ',urllib.parse.unquote(parsed.path.rsplit('/',1)[-1]))
        # The anchor text names the document; a bare "Agenda" link falls back to its file name.
        text=next((t for t in (label,name) if wanted.search(t)),None)
        if not text or TRPA_NOT_MEETING_DOCUMENT.search(label+' '+name):continue
        stated=MONTH_DAY.search(text)
        if stated and (datetime.strptime(stated[1][:3].title(),'%b').month,int(stated[2]))!=(when.month,when.day):continue
        kind='agenda-packet' if wanted.search(text)['kind'].lower()=='packet' else 'agenda'
        found.setdefault(kind,{'id':f'{agency}-{kind}-{day}','title':label or text,'url':url})
    docs=[found[k] for k in ('agenda','agenda-packet') if k in found]
    return docs,bool(docs)


def candidate(agency, sid, title, day, url, docs, text=''):
    return {'agency':agency,'source_id':sid,'title':title,'date':day,
            'start_time':time_in(text),'official_url':url,'documents':docs,
            'status':'canceled' if re.search(r'\bcancel(?:led|ed|lation|ation)\b',text,re.I) else 'unknown',
            'warnings':[]}


def discover_configured(agency, config, *, limit=5, fetch=fetch_text):
    """The newest candidates (at most ``limit``) from one agency's `discovery` block."""
    # limit=None lists every row the index shows (used by the backfill's date-range discovery).
    if limit is not None and not 1<=limit<=20:raise ValueError('discovery limit must be 1..20')
    kind=config['type']
    if kind=='wordpress-meetings':return wordpress_meetings(agency,config,limit=limit,fetch=fetch)
    if kind=='granicus-row-ids':return granicus_row_ids(agency,config,limit=limit,fetch=fetch)
    if kind=='municode':return municode(agency,config,limit)
    url=config.get('indexUrlTemplate',config['indexUrl']).replace('{year}',str(datetime.now(ZoneInfo('America/Los_Angeles')).year))
    markup=fetch(url)
    body=re.compile(config.get('bodyPattern','.'),re.I)
    if kind=='civicclerk':
        return civicclerk(agency,config,markup,limit,fetch)
    embedded=document(markup)
    if kind=='link-index':
        # Follow only public library embeds, never arbitrary scripts or links.
        sheet=next((n.attrs.get('src') for n in embedded.find('iframe') if n.attrs.get('src','').startswith('https://docs.google.com/spreadsheets/d/e/')),None)
        if sheet:
            sheet=sheet.split('/pubhtml')[0]+'/pubhtml/sheet?headers=false&gid=0'
            markup=fetch(sheet);url=sheet
        if '/pubhtml/sheet' in url:
            return sheet_meetings(agency,markup,url,limit)
    tree=document(markup)
    rows,extras=[],[]
    if kind=='dated-table':
        # These official annual indexes put dates in column one, sometimes
        # without a year or as a multi-day range. Use its first day only.
        year=re.search(r'(?<!\d)(20\d{2})(?!\d)',url)
        for tr in tree.find('tr'):
            if any(tr.find('tr')):continue
            cells=[n for n in tr.children if getattr(n,'tag',None)=='td']
            if not cells:continue
            date_text=cells[0].text()
            # Optional: a page can hold several tables (for example workshops) with other date styles.
            if config.get('datePattern') and not re.search(config['datePattern'],date_text):continue
            first_day=re.sub(r'(\d{1,2})\s*[-–]\s*\d{1,2}',r'\1',date_text)
            first_day=re.sub(r'\b(\d{1,2}/\d{1,2})/(\d{2})\b',r'\1/20\2',first_day)
            if not re.search(r'\b20\d{2}\b',first_day) and year:
                found=re.search(r'\b([A-Za-z]+\s+\d{1,2})\b',first_day)
                first_day=(found[1]+' '+year[1]) if found else first_day
            day=date_in(first_day)
            if not day:continue
            docs=[]
            for a in tr.find('a'):
                label=a.text()
                if not re.fullmatch(r'Agenda(?:\s*\(PDF\))?',label,re.I):continue
                target=urllib.parse.urljoin(url,a.attrs.get('href',''))
                docs.extend(documents_in(f'<a href="{html.escape(target,quote=True)}">Agenda (PDF)</a>',url,agency))
            name=next((n.text() for cell in cells[1:] for n in cell.find('strong') if body.search(n.text())),None)
            title=name or config.get('title') or agency.replace('-',' ').title()+' Meeting'
            row=candidate(agency,day,title,day,url,docs,date_text)
            for a in tr.find('a'):
                label,target=a.text(),urllib.parse.urljoin(url,a.attrs.get('href',''))
                if re.fullmatch(r'(?:Draft\s+)?Minutes(?:\s*\(PDF\))?',label,re.I):row.update(listing_minutes(target,label))
                elif re.search(r'\bvideo\b',label,re.I) and (recording:=recording_link(target)):row['recording_url']=recording
            if re.search(r'\d\s*[-–]\s*\d',date_text):
                row['warnings'].append('Multi-day meeting: the date shown is the first day; check the official agenda for subsequent sessions.')
            rows.append(row)
    elif kind=='legistar':
        for tr in tree.find('tr'):
            if any(tr.find('tr')):continue
            text=tr.text()
            if 'DepartmentDetail.aspx' not in url and not body.search(text):continue
            links=[(urllib.parse.urljoin(url,a.attrs.get('href','')),a.text()) for a in tr.find('a')]
            detail=next((u for u,l in links if 'MeetingDetail.aspx' in u),None)
            if not detail:continue
            qs=urllib.parse.parse_qs(urllib.parse.urlparse(detail).query)
            sid=qs.get('ID',[None])[0];day=date_in(text)
            if not sid or not day:continue
            docs=documents_in(''.join(f'<a href="{html.escape(u,quote=True)}">{html.escape(l)}</a>' for u,l in links),url,agency)
            row=candidate(agency,sid,config.get('title') or agency.replace('-', ' ').title()+' Meeting',day,detail,docs,text)
            # The video opens from an onclick handler. Minutes come from the web API
            # (legistar_links), because department pages link minutes before they are final.
            video=re.search(r"Video\.aspx\?[^'\"\s]*ID1=\d+[^'\"\s]*",' '.join(' '.join(a.attrs.values()) for a in tr.find('a')))
            if video:row['recording_url']=urllib.parse.urljoin(url,video[0])
            rows.append(row)
    elif kind=='granicus':
        for tr in tree.find('tr'):
            if any(tr.find('tr')):continue
            text=tr.text()
            ids=' '.join(n.attrs.get('id','') for n in tr.find())
            if not body.search(text+' '+ids):continue
            links=[(urllib.parse.urljoin(url,a.attrs.get('href','')),a.text()) for a in tr.find('a')]
            agenda=next((u for u,l in links if re.search(r'(?:Generated)?AgendaViewer.php',u,re.I)),None)
            if not agenda:continue
            q=urllib.parse.parse_qs(urllib.parse.urlparse(agenda).query)
            sid=q.get('clip_id',q.get('event_id',[None]))[0];day=date_in(text) or date_in(ids)
            if not sid or not day:continue
            row_markup=''.join(f'<a href="{html.escape(u,quote=True)}">{html.escape(l)}</a>' for u,l in links)
            docs=documents_in(row_markup,url,agency)
            if not any(d['url']==agenda for d in docs):docs.insert(0,{'id':agency+'-agenda-html','title':'Official agenda (HTML)','url':agenda})
            cells=[n for n in tr.children if getattr(n,'tag',None)=='td']
            title=config.get('title') or config.get('titlePrefix','')+(cells[0].text() if cells else text[:180])
            row=candidate(agency,sid,title,day,agenda,docs,text)
            row.update(granicus_links(links,' '.join(' '.join(a.attrs.values()) for a in tr.find('a'))+' '+text,url))
            if re.search(r'\d{2}h\s+\d{2}m',text) or any('MediaPlayer.php' in ' '.join(a.attrs.values()) for a in tr.find('a')):
                row['start_time']=None
                row['warnings'].append('Archive playback timestamps are not used as scheduled start times; see the official agenda.')
            rows.append(row)
    elif kind in {'civicplus','link-index','trpa'}:
        # Each linked agenda is its own dated record. Body selection must be
        # scoped by the configured category index, not a whole-county RSS feed.
        for target,label in anchors(markup,url):
            if kind=='civicplus' and not re.search(r'/AgendaCenter/(?:ViewFile/Agenda|PreviousVersions)/',target,re.I):continue
            if kind=='trpa' and not body.search(label):continue
            if kind=='link-index' and not (body.search(label+' '+target) or body.search(tree.text())):continue
            if not urllib.parse.urlparse(target).scheme=='https':continue
            # Rolling indexes may contain minutes as well as agendas; never create
            # a new meeting from the publication date of an unrelated attachment.
            if kind=='link-index' and not re.search(r'agenda',label+' '+target,re.I):continue
            day=date_in(label) or date_in(urllib.parse.unquote(target))
            if not day:continue
            if kind=='civicplus' and 'PreviousVersions' in target:continue
            links,complete={},False
            if kind=='trpa':
                detail=fetch(target);docs,complete=trpa_documents(agency,config,detail,target,day)
                title=label
                links,earlier=page_links(agency,body,day,detail,target)
                extras.extend(earlier)
            else:
                docs=documents_in(f'<a href="{html.escape(target,quote=True)}">{html.escape(label)}</a>',url,agency)
                title=config.get('title') or label
            if not docs and kind!='trpa':continue
            sid=config.get('idPrefix','')+day
            if kind=='link-index' and config.get('qualifySpecial'):
                # Opt-in: a special meeting or workshop shares the index with regular
                # meetings and may fall on the same date, so give it its own ID and title.
                found=re.search(r'\b(special|workshop|joint)\b',urllib.parse.unquote_plus(target),re.I)
                if found:
                    sid+='-'+found[1].lower();title=title.replace(' Board ',f' {found[1].title()} Board ',1)
            rows.append({**candidate(agency,sid,title,day,target,docs,label),**links})
            if complete:rows[-1]['documents_complete']=True  # stale documents (another body's agenda) stop being current
        if kind=='link-index' and config.get('minutesLinks'):
            extras.extend(index_minutes(agency,tree,url,rows))
    elif kind=='meeting-files':
        # Dated meetings (table rows or a "next meeting" paragraph), each with a link to a
        # page listing that meeting's files. Documents are read from those pages below.
        label=re.compile(config.get('filesLabel','View Files'),re.I)
        for node in [*tree.find('tr'),*tree.find('p')]:
            if any(node.find('tr')):continue
            files=[urllib.parse.urljoin(url,a.attrs.get('href','')) for a in node.find('a') if label.search(a.text())]
            day=date_in(node.text())
            if len(files)!=1 or not day:continue
            sid=urllib.parse.urlparse(files[0]).path.strip('/').split('/')
            row=candidate(agency,sid[1] if len(sid)>1 and sid[1].isdigit() else day,config.get('title') or agency.replace('-',' ').title()+' Meeting',day,files[0],[],node.text())
            rows.append(row)
    elif kind=='rss':
        # Use a category-specific feed; do not silently accept unrelated bodies.
        root=ET.fromstring(markup)
        for item in root.findall('.//item'):
            title=item.findtext('title') or '';target=item.findtext('link') or ''
            description=item.findtext('description') or ''
            if not body.search(title+' '+description):continue
            day=date_in(title+' '+description+' '+target)
            if not day:continue
            detail=fetch(target)
            # Special meetings and workshops can share a date with the regular
            # meeting; keep them distinct, matching the manual ID convention.
            qualifier=re.search(r'\b(special|workshop|joint)\b',title,re.I)
            sid=day+('-'+qualifier[1].lower() if qualifier else '')
            # Drop the trailing "(Tuesday, October 13, 2026, 2:00 p.m. @ ...)" from the display title.
            shown=config.get('titlePrefix','')+re.sub(r'\s*(?:[-–]\s*)?\((?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,[^)]*\)\s*$','',title)
            rows.append(candidate(agency,sid,shown,day,target,documents_in(detail,target,agency),title+' '+description))
    else:raise ValueError(f'unsupported source type: {kind}')
    if config.get('horizonDays'):
        # Placeholder calendars list dates far ahead that would crowd out the near-term meetings below.
        cutoff=(datetime.now(ZoneInfo('America/Los_Angeles')).date()+timedelta(days=config['horizonDays'])).isoformat()
        rows=[r for r in rows if not r['date'] or r['date']<=cutoff]
    unique={}
    for row in rows:
        unique.setdefault(row['source_id'],row)
    result=sorted(unique.values(),key=lambda r:r['date'] or '',reverse=True)[:limit]
    if kind=='meeting-files':
        for row in result:
            row['documents']=meeting_files(agency,row,fetch)
    if kind=='legistar':
        legistar_links(config,result,fetch)
    return result+extras


def page_links(agency, body, day, markup, base):
    """Minutes and recording links a meeting page gives for this body.

    Returns links for the page's own date, and records-only entries for minutes of
    earlier meetings ("Draft September 23, 2026 Governing Board Minutes" on the
    October page). Labels must name the body and a date; minutes must say final or draft.
    """
    own,earlier={},[]
    for target,label in anchors(markup,base):
        if not body.search(label):continue
        named=date_in(label)
        if re.search(r'\bminutes\b',label,re.I) and re.match(r'\s*(?:final|draft)\b',label,re.I):
            links=listing_minutes(target,label)
            if named==day:own.update(links)
            elif named and named<day:earlier.append(records_entry(agency,named,label,links))
        elif named==day and re.search(r'\b(?:video|recording)\b',label,re.I) and (recording:=recording_link(target)):
            own['recording_url']=recording
    return own,earlier


def index_minutes(agency, tree, base, rows):
    """Minutes PDFs linked on a link index, matched by date to that index's agenda rows.

    The date comes from the link's aria-label ("Minutes attachment for 2026-09-03
    Board Meeting"), its label, or its file name. Minutes whose meeting the index
    no longer lists become records-only entries.
    """
    extras=[]
    for node in tree.find('a'):
        label,target=node.text(),urllib.parse.urljoin(base,node.attrs.get('href',''))
        parsed=urllib.parse.urlparse(target)
        if not re.search(r'\bminutes\b',label,re.I) or parsed.scheme!='https' or not parsed.path.lower().endswith('.pdf'):continue
        aria=node.attrs.get('aria-label','');name=urllib.parse.unquote_plus(parsed.path)
        day=date_in(aria) or date_in(label) or date_in(name)
        if not day:continue
        links=listing_minutes(target,label)
        same={r['source_id']:r for r in rows if r['date']==day}
        if len(same)>1:
            quals=qualifiers(aria+' '+label+' '+name)
            same={sid:r for sid,r in same.items() if qualifiers(r['title'])==quals}
        if len(same)==1:
            sid=next(iter(same))
            for row in rows:
                if row['source_id']==sid:row.update(links)
        elif not same:
            extras.append(records_entry(agency,day,label,links))
    return extras


def legistar_events(config, since, until, *, fetch=fetch_text, page_size=1000, max_pages=10):
    """(client, events dated since..until whose body matches) from the public Legistar web API."""
    client=config.get('legistarClient') or urllib.parse.urlparse(config['indexUrl']).hostname.split('.')[0]
    body=re.compile(config.get('bodyPattern','.'),re.I)
    flt=urllib.parse.quote(f"EventDate ge datetime'{since.isoformat()}' and EventDate le datetime'{until.isoformat()}'")
    found=[]
    for page in range(max_pages):
        events=json.loads(fetch(f'https://webapi.legistar.com/v1/{client}/events?$filter={flt}&$orderby=EventDate%20desc&$top={page_size}&$skip={page*page_size}'))
        found.extend(e for e in events if body.search(e.get('EventBodyName') or ''))
        if len(events)<page_size:break
    return client,found


def event_links(client, event):
    """Minutes (final or draft, with a file) and the Granicus video of one Legistar event."""
    links={}
    minutes=event.get('EventMinutesFile') or ''
    status=(event.get('EventMinutesStatusName') or '').lower()
    if minutes.startswith('https://') and status in ('final','draft'):
        links.update(minutes_url=minutes,minutes_status='approved' if status=='final' else 'draft',minutes_note=None)
    if event.get('EventMedia'):
        links['recording_url']=f"https://{client}.legistar.com/Video.aspx?Mode=Granicus&ID1={event['EventMedia']}&Mode2=Video"
    return links


def legistar_links(config, rows, fetch):
    """Add web API minutes and video to department-page rows, matched by date; a failing API adds nothing."""
    days=sorted(r['date'] for r in rows if r.get('date'))
    if not days:return
    until=date.fromisoformat(days[-1])
    try:
        client,events=legistar_events(config,max(date.fromisoformat(days[0]),until-timedelta(days=400)),until,fetch=fetch)
    except Exception:  # The department page alone still gives meetings and video.
        return
    by_day={}
    for event in events:
        by_day.setdefault((event.get('EventDate') or '')[:10],[]).append(event)
    for row in rows:
        same=by_day.get(row.get('date'),[])
        if len(same)==1:row.update(event_links(client,same[0]))


def meeting_files(agency, row, fetch):
    """Read the agenda from a meeting's file-list page; staff-report tabs and minutes stay on the official page."""
    page=fetch(row['official_url'])
    qualifier=re.search(r'\((Special|Workshop|Joint)\)',page)
    if qualifier:row['title']=row['title'].replace('Regular',qualifier[1])
    docs=[]
    for node in document(page).find('a'):
        target=urllib.parse.urljoin(row['official_url'],node.attrs.get('href',''))
        name=urllib.parse.parse_qs(urllib.parse.urlparse(target).query).get('file',[''])[0]
        if urllib.parse.urlparse(target).scheme=='https' and re.match(r'agenda\b',name,re.I) and name.lower().endswith('.pdf'):
            docs.append({'id':agency+'-agenda','title':node.text() or 'Official agenda','url':target})
            break
    if not docs:row['warnings'].append('No agenda file is listed on the official meeting page yet.')
    return docs


def civicclerk(agency, config, markup, limit, fetch):
    api=config.get('apiUrl') or 'https://'+urllib.parse.urlparse(config['indexUrl']).hostname.split('.')[0]+'.api.civicclerk.com/v1'
    if markup.lstrip().startswith('<'):
        markup=fetch(api+"/Events/GetEventsList(fromDate='',toDate='',categoryIds=[],skip=0,sortDescending=true,cursorId=0)")
    data=json.loads(markup)
    records=data.get('value',data.get('items',[])) if isinstance(data,dict) else data
    rows=[]
    portal=config.get('portalUrl') or config['indexUrl']
    for event in records:
        title=event.get('eventName') or event.get('name') or event.get('title') or ''
        if not re.search(config.get('bodyPattern','.'),title,re.I):continue
        raw=event.get('eventDate') or event.get('date') or event.get('startDate') or ''
        day=date_in(raw);eid=event.get('id') or event.get('eventId')
        if not day or not eid:continue
        docs=[]
        for file in event.get('publishedFiles',[]):
            if file.get('type') not in {'Agenda','Agenda Packet'}:continue
            fid=file['fileId']
            docs.append({'id':agency+'-agenda' if file['type']=='Agenda' else agency+'-agenda-packet',
                'title':file.get('name') or file['type'],
                'url':api+f'/Meetings/GetMeetingFileStream(fileId={fid},plainText=false)'})
        docs.sort(key=lambda d:'packet' in d['id'])
        row=candidate(agency,str(eid),title,day,portal.rstrip('/')+f'/event/{eid}/overview',docs,title)
        minutes=[f for f in event.get('publishedFiles',[]) if f.get('type')=='Minutes' and f.get('fileId')]
        if minutes:
            row.update(listing_minutes(api+f"/Meetings/GetMeetingFileStream(fileId={minutes[-1]['fileId']},plainText=false)",minutes[-1].get('name') or ''))
        if event.get('hasMedia'):row['recording_url']=portal.rstrip('/')+f'/event/{eid}/media'
        row['warnings']=['Confirm the scheduled start in the agenda; CivicClerk wall-clock timestamps are not treated as UTC instants.']
        rows.append(row)
    return sorted(rows,key=lambda r:r['date'],reverse=True)[:limit]


def sheet_meetings(agency, markup, url, limit):
    rows=[]
    for tr in document(markup).find('tr'):
        day=date_in(tr.text())
        if not day:continue
        docs=[]
        for node in tr.find('a'):
            target=node.attrs.get('href','');label=node.text()
            if not re.search('agenda|packet|minutes',label,re.I):continue
            parsed=urllib.parse.urlparse(target)
            if parsed.hostname=='www.google.com':target=urllib.parse.parse_qs(parsed.query).get('q',[''])[0]
            if not target.startswith('https://'):continue
            role='agenda-packet' if 'packet' in label.lower() else 'minutes' if 'minutes' in label.lower() else 'agenda'
            docs.append({'id':agency+'-'+role,'title':label,'url':target})
        if not docs:continue
        docs.sort(key=lambda d:('minutes' in d['id'],'packet' in d['id']))
        row=candidate(agency,day,agency.upper()+' Board meeting',day,url,docs)
        if minutes:=next((d for d in docs if d['id'].endswith('-minutes')),None):row.update(listing_minutes(minutes['url'],minutes['title']))
        rows.append(row)
    return sorted(rows,key=lambda r:r['date'],reverse=True)[:limit]


def _municode_client(config):
    """The client code in a Municode meetings page URL (``.../MeetingsPage/index?clientCode=TRUCKEECA``)."""
    return urllib.parse.parse_qs(urllib.parse.urlparse(config['indexUrl']).query).get('clientCode',[None])[0]


def _municode_meetings(agency, config, since, until, fetch_json=None):
    """Municode meetings dated since..until whose name matches `bodyPattern`, newest first, from its public API."""
    def default(payload):
        raw,_=fetch_bytes('https://meetings.municode.com/api/publicMeetings/list',max_bytes=6*1024*1024,accept='application/json',payload=payload)
        return json.loads(raw)
    client=_municode_client(config)
    rows=[]
    for meeting in (fetch_json or default)(json.dumps({'displayClientCode':client,'isDraft':False,'meetingTypeIds':[],
                                                       'meetingDateFrom':since.isoformat(),'meetingDateTo':until.isoformat()}).encode()):
        title=meeting.get('meeting') or meeting.get('meetingType') or ''
        if not re.search(config.get('bodyPattern','.'),title,re.I):continue
        day=date_in(meeting.get('date',''))
        if not day:continue
        sid=meeting['meetingId'];docs=[]
        for key,label in [('adaHTMLAgenda','Official agenda (HTML)'),('agenda','Official agenda (PDF)')]:
            if meeting.get(key):docs.append({'id':agency+'-agenda-'+('html' if key=='adaHTMLAgenda' else 'pdf'),'title':label,'url':meeting[key]})
        official='https://meetings.municode.com/MeetingDetailsPage/index?clientCode='+client+'&meetingId='+sid
        row=candidate(agency,sid,title,day,official,docs,meeting.get('time',''))
        row['location']=meeting.get('venue')
        rows.append(row)
    return sorted(rows,key=lambda r:r['date'],reverse=True)


def municode(agency, config, limit, *, fetch_json=None):
    """Meetings within 90 days either side of today (the API takes a date range)."""
    today=datetime.now(ZoneInfo('America/Los_Angeles')).date()
    return _municode_meetings(agency,config,today-timedelta(days=90),today+timedelta(days=90),fetch_json)[:limit]


# ---------------------------------------------------------------- date-range discovery (backfill)
# These wrap the bounded adapters above without changing them. Each returns
# (candidates dated since..until, earliest date the source listed or None).

def _memo(fetch):
    cache={}
    def cached(url,*args,**kwargs):
        if args or kwargs:return fetch(url,*args,**kwargs)
        if url not in cache:cache[url]=fetch(url)
        return cache[url]
    return cached


def _in_range(rows, since, until):
    days=[r['date'] for r in rows if r.get('date')]
    earliest=min(days) if days else None
    return [r for r in rows if r.get('date') and since.isoformat()<=r['date']<=until.isoformat()], earliest


def municode_range(agency, config, since, until, *, fetch_json=None):
    """Every Municode meeting dated since..until (the API takes a date range)."""
    return _in_range(_municode_meetings(agency,config,since,until,fetch_json),since,until)


def civicclerk_range(agency, config, since, until, *, fetch=fetch_text, max_pages=40):
    """Page the CivicClerk event list (newest first) until events are older than ``since``."""
    api=config.get('apiUrl') or 'https://'+urllib.parse.urlparse(config['indexUrl']).hostname.split('.')[0]+'.api.civicclerk.com/v1'
    rows,earliest=[],None
    skip=0
    for _ in range(max_pages):
        data=json.loads(fetch(api+f"/Events/GetEventsList(fromDate='',toDate='',categoryIds=[],skip={skip},sortDescending=true,cursorId=0)"))
        records=data.get('value',data.get('items',[])) if isinstance(data,dict) else data
        if not records:break
        page=civicclerk(agency,config,json.dumps({'items':records}),None,fetch)
        rows.extend(page)
        days=[d for d in (date_in(e.get('eventDate') or e.get('date') or e.get('startDate') or '') for e in records) if d]
        if days:earliest=min([earliest or days[0],*days])
        skip+=len(records)
        if (days and min(days)<since.isoformat()) or not (isinstance(data,dict) and data.get('hasMore')):break
    unique={r['source_id']:r for r in rows}
    found,_=_in_range(sorted(unique.values(),key=lambda r:r['date'],reverse=True),since,until)
    return found,earliest


def legistar_range(agency, config, since, until, *, fetch=fetch_text, page_size=1000, max_pages=10):
    """Legistar events dated since..until from the public web API, filtered by body name.

    Source IDs are the API's EventId, which differs from the MeetingDetail ID on
    department pages; capture's source_identity bridges a same-date stored meeting.
    """
    client,events=legistar_events(config,since,until,fetch=fetch,page_size=page_size,max_pages=max_pages)
    rows=[]
    for event in events:
        day=(event.get('EventDate') or '')[:10]
        if not day or not event.get('EventId'):continue
        docs=[]
        if (event.get('EventAgendaFile') or '').startswith('https://'):
            docs.append({'id':agency+'-agenda','title':'Agenda','url':event['EventAgendaFile']})
        url=event.get('EventInSiteURL') or config['indexUrl']
        text=' '.join(str(event.get(k) or '') for k in ('EventTime','EventComment','EventAgendaStatusName'))
        row=candidate(agency,str(event['EventId']),config.get('title') or agency.replace('-', ' ').title()+' Meeting',day,url,docs,text)
        rows.append({**row,**event_links(client,event)})
    return sorted(rows,key=lambda r:r['date'],reverse=True)


def discover_configured_range(agency, config, *, since, until, fetch=fetch_text, municode_json=None):
    """Every configured-source candidate dated since..until.

    Returns (candidates, earliest date listed, queried_by_date). Full-listing
    indexes (Granicus, link indexes, TRPA) are read whole; yearly indexes are
    read once per year in the range; Municode and the Legistar web API are
    queried by date range (queried_by_date=True: they reach ``since`` by
    design); CivicClerk and WordPress posts are paged until older than ``since``.
    """
    fetch=_memo(fetch)
    kind=config['type']
    if kind=='wordpress-meetings':
        return (*wordpress_meetings_range(agency,config,since=since,until=until,fetch=fetch),False)
    if kind=='granicus-row-ids':
        return (*granicus_row_ids_range(agency,config,since=since,until=until,fetch=fetch),False)
    if kind=='municode':
        return (*municode_range(agency,config,since,until,fetch_json=municode_json),True)
    if kind=='civicclerk':
        found,earliest=civicclerk_range(agency,config,since,until,fetch=fetch)
        return found,earliest,False
    template=config.get('indexUrlTemplate')
    if template and '{year}' in template:
        rows=[]
        for year in range(since.year,until.year+1):
            rows+=discover_configured(agency,{**config,'indexUrlTemplate':template.replace('{year}',str(year))},limit=None,fetch=fetch)
        return (*_in_range(rows,since,until),True)
    if kind=='legistar':
        try:
            api=legistar_range(agency,config,since,until,fetch=fetch)
        except (OSError,RuntimeError,ValueError):
            api=None  # Fall back to the department page alone.
        if api is not None:
            page,_=_in_range(discover_configured(agency,config,limit=None,fetch=fetch),since,until)
            covered={r['date'] for r in api}
            rows=api+[r for r in page if r['date'] not in covered]
            return (*_in_range(rows,since,until),True)
    return (*_in_range(discover_configured(agency,config,limit=None,fetch=fetch),since,until),False)
