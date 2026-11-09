"""Exact source-backed headings from explicit Granicus, Municode, and "Order of Business" agenda structures."""
import re
from . import html_order_of_business
from .html_dom import document, Node
from .outline import section_name


def _quote(source, title):
    parts=title.split()
    if not parts:return None
    found=re.search(r'\s+'.join(re.escape(part) for part in parts),source)
    return found.group() if found else None


def _full(whole, title):
    # The whole heading when the 280-character bound cut it (the title itself is unchanged).
    return whole if whole!=title and len(whole)>len(title) else None


def parse_html_agenda(markup, text):
    tree=document(markup)
    if html_order_of_business.recognizes(tree):
        return html_order_of_business.parse(markup, text)
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
                whole=title.split(' : ',1)[0].strip()
                title=whole[:280]
                section=title
                entries.append((number,title,section,_full(whole,title)))
                if title.lower().startswith('adjournment'):break
            elif node.tag=='num' and re.fullmatch(r'\d+\.\d+',node.text()):
                # The first span after this number is the title, before the
                # separately marked Recommended Action and attachments.
                # Locate the p containing this exact num node.
                parent=next((p for p in tree.find('p') if node in list(p.find('num'))),None)
                if not parent:continue
                title=next((n.text() for n in parent.children if isinstance(n,Node) and n.tag=='span'),None)
                if title:entries.append((node.text(),title[:280],section,_full(title,title[:280])))
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
                if not top:return _lettered(tree, text)
                number=f'{top[-1]}.{number}'
            # Long procedural descriptions stay bounded to their own row.
            whole=title.strip()
            title=title[:280].strip()
            entries.append((number,title,section,_full(whole,title)))
            if title.lower().startswith('adjournment'):break
    if not entries or not top or top!=list(range(1,len(top)+1)) or not entries[-1][1].lower().startswith('adjournment'):
        return _lettered(tree, text)
    return _items(entries, text)


def _items(entries, text):
    items=[]
    for number,title,section,full in entries:
        quote=_quote(text,title)
        if not quote:return [],False
        items.append({'source_number':number,'order':len(items)+1,'section':section,
                      'title':title,'source_pages':[{'document_id':'__html__','page_number':1,'quote':quote}]})
        if full:items[-1]['full_title']=full
    return items,True


# A heading's leading capitals: "PUBLIC COMMENT Any person may..." -> "PUBLIC COMMENT", and
# "DEPARTMENT REPORTS (Informational Only)" -> "DEPARTMENT REPORTS" (the outline's ``capsLead``).
_CAPS_LEAD = re.compile(r"^((?:[^a-z\s]+\s+){0,10}?[^a-z\s]*[A-Z][^a-z\s]*)\s+[-–—]?\s*(?=[A-Z][a-z]|\((?=[A-Z][a-z]))")


def _first_line(node):
    """The cell's first line of text, as ``html_text`` breaks it: a heading without the description under it."""
    parts=[]

    def walk(n):
        for child in n.children:
            if not isinstance(child,Node):parts.append(child)
            elif child.tag in {'br','p','div','li','table','tr'}:parts.append('\n');walk(child);parts.append('\n')
            else:walk(child)
    walk(node)
    return next((' '.join(line.split()) for line in ''.join(parts).split('\n') if line.strip()),'')


def _heading(node):
    line=_first_line(node)
    head=_CAPS_LEAD.match(line)
    if head and len(head.group(1))>=4:
        line=head.group(1)
    return line.strip(' -–—:*')


def _granicus_rows(tree):
    """(blockquote depth, label, bold, title cell) of every Granicus ``numberspace`` row, in page order."""
    rows=[]

    def walk(node, depth):
        for child in node.children:
            if not isinstance(child,Node):continue
            if child.tag=='tr':
                cells=[n for n in child.children if isinstance(n,Node) and n.tag=='td']
                if len(cells)==2 and 'numberspace' in cells[0].attrs.get('class',''):
                    rows.append((depth,cells[0].text().rstrip('.'),any(cells[0].find('strong')),cells[1]))
            walk(child, depth+(child.tag=='blockquote'))
    walk(tree,0)
    return rows


def _subtree(rows, k):
    """The rows nested under row ``k`` (deeper blockquotes, up to the next row at its depth or shallower)."""
    end=k+1
    while end<len(rows) and rows[end][0]>rows[k][0]:end+=1
    return rows[k+1:end]


def _signature(rows):
    return [(label,' '.join(cell.text().split())) for _,label,_,cell in rows]


def _lettered(tree, text):
    """Granicus agendas lettered by section (Tahoe City PUD): bold "A." sections, "1." items, "a." sub-items.

    Each level sits one ``blockquote`` deeper than its parent. A section is a bold capital
    letter at the outermost depth; its numbered rows are items ("D.3"), and a section with
    none is itself an item ("A"). Sub-items belong to their item and are not items.

    Granicus sometimes leaves a ``blockquote`` unclosed and prints a section again: once
    nested in the previous item, unbolded, and once more, bold and empty, after the real
    one. A row in the wrong place (a letter that is not bold or not at the section depth),
    or a bold section repeating the one before it, is skipped with everything under it, but
    only when it repeats a real section verbatim; any other irregularity rejects the page.
    """
    rows=_granicus_rows(tree)
    sections=[k for k,(depth,label,bold,_) in enumerate(rows) if bold and re.fullmatch(r'[A-Z]',label)]
    if not sections or rows[sections[0]][1]!='A':return [],False
    base=rows[sections[0]][0]
    real={}  # letter -> (heading, subtree signature) of each section's first bold printing
    for k in sections:
        if rows[k][0]==base:
            real.setdefault(rows[k][1],(_heading(rows[k][3]),_signature(_subtree(rows,k))))
    entries=[]  # [letter, heading, [(number, title)]]
    k=sections[0]
    while k<len(rows):
        depth,label,bold,cell=rows[k]
        if re.fullmatch(r'[A-Z]',label):
            nested=_subtree(rows,k)
            repeat=real.get(label)==(_heading(cell),_signature(nested)) or (
                not nested and entries and entries[-1][0]==label and entries[-1][1]==_heading(cell))
            if bold and depth==base and (not entries or label==chr(ord(entries[-1][0])+1)):
                entries.append([label,_heading(cell),[]])
                k+=1
                continue
            if not repeat or not entries:return [],False
            k+=1+len(nested)  # a printed-again section: skip it and its rows
            continue
        if depth<=base or not entries:return [],False
        if re.fullmatch(r'\d+',label):
            numbered=entries[-1][2]
            if depth!=base+1 or int(label)!=len(numbered)+1:return [],False
            numbered.append((label,_first_line(cell)))
        elif not (re.fullmatch(r'[a-z]',label) and depth>=base+2 and entries[-1][2]):
            return [],False
        k+=1
    if not entries or not entries[-1][1].lower().startswith('adjournment'):return [],False
    # Titles are whole, as the outline engine reads this format from the page text.
    flat=[]
    for letter,heading,numbered in entries:
        flat+=[(f'{letter}.{number}',title,section_name(heading),None) for number,title in numbered] or [(letter,heading,None,None)]
    return _items(flat, text)
