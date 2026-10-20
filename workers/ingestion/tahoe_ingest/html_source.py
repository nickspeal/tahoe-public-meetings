"""Readable HTML agenda text, without scripts, menus, or invented PDF pages."""
from html.parser import HTMLParser
import re


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.skip = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = tag in {'script', 'style', 'noscript', 'nav', 'header', 'footer'} or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
        if tag not in {'br', 'hr', 'img', 'input', 'meta', 'link', 'wbr', 'source'}:
            self.skip.append((tag, hidden))
            self.hidden += hidden
        if not self.hidden and tag in {'br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'section', 'table'}:
            self.parts.append('\n')
        if tag in {'td', 'th'} and not self.hidden:
            self.parts.append('  ')

    def handle_endtag(self, tag):
        for pos in range(len(self.skip) - 1, -1, -1):
            if self.skip[pos][0] == tag:
                self.hidden -= sum(hidden for _, hidden in self.skip[pos:])
                del self.skip[pos:]
                break
        if not self.hidden and tag in {'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'section', 'table'}:
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr", "source"}:
            self.handle_endtag(tag)

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def html_text(markup: str) -> str:
    parser = Text()
    parser.feed(markup)
    lines = [re.sub(r'[ \t\r\f\v]+', ' ', line).strip() for line in ''.join(parser.parts).split('\n')]
    return '\n'.join(line for line in lines if line)
