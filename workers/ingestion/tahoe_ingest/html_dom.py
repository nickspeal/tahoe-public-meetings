"""Small read-only HTML tree for official source tables and agenda headings."""
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def find(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.find(tag)

    def text(self):
        if self.tag in {'script','style','noscript'}:
            return ''
        return ' '.join(' '.join(child.text() if isinstance(child, Node) else child for child in self.children).split())

class DOM(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root=Node('document')
        self.stack=[self.root]

    def handle_starttag(self, tag, attrs):
        node=Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for n in range(len(self.stack)-1,0,-1):
            if self.stack[n].tag==tag:
                del self.stack[n:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def document(markup):
    parser=DOM();parser.feed(markup)
    return parser.root
