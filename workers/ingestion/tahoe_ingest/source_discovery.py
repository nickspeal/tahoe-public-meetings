"""Configured official indexes: CivicPlus, Legistar, Granicus, and link feeds.

URLs and body filters are operator configuration, never instructions from HTML.
Dates must be written on a source row or document filename, not inferred from
regular meeting patterns. Agenda-only documents are preferred over full packets.
"""
from __future__ import annotations
import html
import hashlib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .http import fetch_bytes
from .discovery import anchors, fetch_text, _pdf_links
from .html_dom import document


def date_in(text):
    text = text.replace('_', ' ')
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
    # Prefer accessible HTML agenda / small agenda PDF to full packets and minutes.
    docs.sort(key=lambda d: (bool(re.search(r'how to|help guide|staff report|agenda item|public comment',d['title'],re.I)) or 'attachment' in d['id'],
                             'packet' in (d['title']+' '+d['id']).lower(),
                             'minutes' in (d['title']+' '+d['id']).lower(),
                             not bool(re.search(r'html|AADA',d['title']+' '+d['url'],re.I))))
    counts={i:sum(d['id']==i for d in docs) for i in {d['id'] for d in docs}}
    for d in docs:
        if counts[d['id']]>1:d['id']+='-'+hashlib.sha256(d['url'].encode()).hexdigest()[:10]
    return docs


def candidate(agency, sid, title, day, url, docs, text=''):
    return {'agency':agency,'source_id':sid,'title':title,'date':day,
            'start_time':time_in(text),'official_url':url,'documents':docs,
            'status':'canceled' if re.search(r'\bcancel(?:led|ed|lation|ation)\b',text,re.I) else 'unknown',
            'warnings':[]}


def discover_configured(agency, config, *, limit=5, fetch=fetch_text):
    if not 1<=limit<=20:raise ValueError('discovery limit must be 1..20')
    url=config.get('indexUrlTemplate',config['indexUrl']).replace('{year}',str(datetime.now(ZoneInfo('America/Los_Angeles')).year));kind=config['type']
    markup=fetch(url)
    body=re.compile(config.get('bodyPattern','.'),re.I)
    if kind=='civicclerk':
        return civicclerk(agency,config,markup,limit,fetch)
    embedded=document(markup)
    if kind=='civicplus' and any('meetings.municode.com/MeetingsPage/' in n.attrs.get('src','') for n in embedded.find('iframe')):
        return municode(agency,config,limit)
    if kind=='link-index':
        # Follow only public library embeds, never arbitrary scripts or links.
        sheet=next((n.attrs.get('src') for n in embedded.find('iframe') if n.attrs.get('src','').startswith('https://docs.google.com/spreadsheets/d/e/')),None)
        if sheet:
            sheet=sheet.split('/pubhtml')[0]+'/pubhtml/sheet?headers=false&gid=0'
            markup=fetch(sheet);url=sheet
        if '/pubhtml/sheet' in url:
            return sheet_meetings(agency,markup,url,limit)
    tree=document(markup)
    rows=[]
    if kind=='dated-table':
        # These official annual indexes put dates in column one, sometimes
        # without a year or as a multi-day range. Use its first day only.
        year=re.search(r'(?<!\d)(20\d{2})(?!\d)',url)
        for tr in tree.find('tr'):
            if any(tr.find('tr')):continue
            cells=[n for n in tr.children if getattr(n,'tag',None)=='td']
            if not cells:continue
            date_text=cells[0].text()
            first_day=re.sub(r'(\d{1,2})\s*[-–]\s*\d{1,2}',r'\1',date_text)
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
            rows.append(candidate(agency,sid,config.get('title') or agency.replace('-', ' ').title()+' Meeting',day,detail,docs,text))
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
            if kind=='trpa':
                detail=fetch(target);docs=documents_in(detail,target,agency)
                title=label
            else:
                docs=documents_in(f'<a href="{html.escape(target,quote=True)}">{html.escape(label)}</a>',url,agency)
                title=config.get('title') or label
            if not docs and kind!='trpa':continue
            rows.append(candidate(agency,config.get('idPrefix','')+day,title,day,target,docs,label))
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
            rows.append(candidate(agency,day,title,day,target,documents_in(detail,target,agency),title+' '+description))
    else:raise ValueError(f'unsupported source type: {kind}')
    unique={}
    for row in rows:
        unique.setdefault(row['source_id'],row)
    return sorted(unique.values(),key=lambda r:r['date'] or '',reverse=True)[:limit]


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
        rows.append(candidate(agency,day,agency.upper()+' Board meeting',day,url,docs))
    return sorted(rows,key=lambda r:r['date'],reverse=True)[:limit]


def municode(agency, config, limit):
    today=datetime.now(ZoneInfo('America/Los_Angeles')).date()
    payload=json.dumps({'displayClientCode':config.get('clientCode','TRUCKEECA'), 'isDraft':False, 'meetingTypeIds':[],
                       'meetingDateFrom':(today-timedelta(days=90)).isoformat(),
                       'meetingDateTo':(today+timedelta(days=90)).isoformat()}).encode()
    raw,_=fetch_bytes('https://meetings.municode.com/api/publicMeetings/list',max_bytes=6*1024*1024,accept='application/json',payload=payload)
    rows=[]
    for meeting in json.loads(raw):
        title=meeting.get('meeting') or meeting.get('meetingType') or ''
        if not re.search(config.get('bodyPattern','Town Council'),title,re.I):continue
        day=date_in(meeting.get('date',''))
        if not day:continue
        sid=meeting['meetingId'];docs=[]
        for key,label in [('adaHTMLAgenda','Official agenda (HTML)'),('agenda','Official agenda (PDF)')]:
            if meeting.get(key):docs.append({'id':agency+'-agenda-'+('html' if key=='adaHTMLAgenda' else 'pdf'), 'title':label,'url':meeting[key]})
        official='https://meetings.municode.com/MeetingDetailsPage/index?clientCode='+config.get('clientCode','TRUCKEECA')+'&meetingId='+sid
        row=candidate(agency,sid,title,day,official,docs,meeting.get('time',''))
        row['location']=meeting.get('venue')
        rows.append(row)
    return sorted(rows,key=lambda r:r['date'],reverse=True)[:limit]
