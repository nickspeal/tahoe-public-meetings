"""Exact source-backed headings from explicit Granicus/Municode agenda structures."""
import re
from .html_dom import document, Node


def _quote(source, title):
    parts=title.split()
    if not parts:return None
    found=re.search(r'\s+'.join(re.escape(part) for part in parts),source)
    return found.group() if found else None


def parse_html_agenda(markup, text):
    tree=document(markup)
    entries=[]
    top=[]
    section='Agenda'
    # Municode's published accessible agenda uses h2.section-header and num tags.
    if any(n.attrs.get('class')=='section-header' for n in tree.find('h2')):
        for node in tree.find():
            if node.tag=='h2' and node.attrs.get('class')=='section-header':
                match=re.match(r'^(\d+)\.\s*(.+)',node.text())
                if not match:continue
                number,title=match.groups();top.append(int(number))
                title=title.split(' : ',1)[0].strip()[:280]
                section=title
                entries.append((number,title,section))
                if title.lower().startswith('adjournment'):break
            elif node.tag=='num' and re.fullmatch(r'\d+\.\d+',node.text()):
                # The first span after this number is the title, before the
                # separately marked Recommended Action and attachments.
                # Locate the p containing this exact num node.
                parent=next((p for p in tree.find('p') if node in list(p.find('num'))),None)
                if not parent:continue
                title=next((n.text() for n in parent.children if isinstance(n,Node) and n.tag=='span'),None)
                if title:entries.append((node.text(),title[:280],section))
    else:
        # Granicus marks each number in td.numberspace, separate from its title.
        for tr in tree.find('tr'):
            if any(tr.find('tr')):continue
            cells=[n for n in tr.children if isinstance(n,Node) and n.tag=='td']
            if len(cells)!=2 or 'numberspace' not in cells[0].attrs.get('class',''):continue
            number=cells[0].text().rstrip('.')
            if not re.fullmatch(r'\d+|[a-z]',number):continue
            title=cells[1].text()
            if number.isdigit():
                top.append(int(number))
                section='Agenda'
            else:
                if not top:return [],False
                number=f'{top[-1]}.{number}'
            # Long procedural descriptions stay bounded to their own row.
            title=title[:280].strip()
            entries.append((number,title,section))
            if title.lower().startswith('adjournment'):break
    if not entries or not top or top!=list(range(1,len(top)+1)) or not entries[-1][1].lower().startswith('adjournment'):
        return [],False
    items=[]
    for number,title,section in entries:
        quote=_quote(text,title)
        if not quote:return [],False
        items.append({'source_number':number,'order':len(items)+1,'section':section,
                      'title':title,'source_pages':[{'document_id':'__html__','page_number':1,'quote':quote}]})
    return items,True
