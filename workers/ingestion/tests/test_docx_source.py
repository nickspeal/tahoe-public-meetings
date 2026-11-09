"""Word (.docx) agendas: text with Word's automatic list numbering rendered, through capture's extraction.

Word stores "A. Call to Order" as the paragraph "Call to Order" plus a list level whose
label lives in word/numbering.xml. The documents below are built here in the shape the
South Lake Tahoe Tourism Improvement District's agendas use (one list whose level 0 is
"A." and whose levels 3 and 5 carry the second "A." list and its roman sub-items).
"""
import io
import zipfile

import pytest

from tahoe_ingest import docx_source, http, source
from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.docx_source import docx_sections, format_number

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def level(ilvl, fmt, text, start=1, restart=None, legal=False):
    return (f'<w:lvl w:ilvl="{ilvl}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
            + (f'<w:lvlRestart w:val="{restart}"/>' if restart is not None else '')
            + ('<w:isLgl/>' if legal else '') + f'<w:lvlText w:val="{text}"/></w:lvl>')


def numbering(*abstracts, nums=()):
    body = ''.join(f'<w:abstractNum w:abstractNumId="{aid}">{"".join(levels)}</w:abstractNum>' for aid, levels in abstracts)
    return f'<w:numbering {NS}>{body}{"".join(nums)}</w:numbering>'


def para(text, num=None, ilvl=0, style=None, extra=''):
    ppr = ''
    if style or num is not None:
        ppr = '<w:pPr>' + (f'<w:pStyle w:val="{style}"/>' if style else '') + (
            f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num}"/></w:numPr>' if num is not None else '') + '</w:pPr>'
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r>{extra}</w:p>'


def docx(body, numbering_xml=None, styles_xml=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', f'<w:document {NS}><w:body>{body}</w:body></w:document>')
        if numbering_xml:
            archive.writestr('word/numbering.xml', numbering_xml)
        if styles_xml:
            archive.writestr('word/styles.xml', styles_xml)
    return out.getvalue()


SLTTID_LIST = numbering(("2", [level(0, "upperLetter", "%1."), level(1, "lowerLetter", "%2."), level(2, "lowerLetter", "%3."),
                               level(3, "upperLetter", "%4."), level(4, "lowerLetter", "%5."), level(5, "lowerRoman", "%6.")]),
                        nums=['<w:num w:numId="1"><w:abstractNumId w:val="2"/></w:num>'])


def slttid_agenda():
    return docx(''.join([
        para("South Lake Tahoe Tourism Improvement District"), para(" Board of Directors Meeting"),
        para("1:00 PM, Thursday Oct 29th, 2026"), para(""),
        *(para(t, 1, 0) for t in ("Call to Order", "Roll Call", "Introduction of Guests and Staff",
                                  "Approval of Minutes of Previous Meeting (Attachment)", "Approval of Agenda",
                                  "Public Comment (Non-Agenda Items, 5-Minute Limit)")),
        para("AGENDA", style="Heading1"), para("", style="ListParagraph"),
        para("Discussion/Possible Action", 1, 3), para(""),
        *(para(t, 1, 5) for t in ("Treasures Report", "Board Nomination Forms", "2026/2027 Budget Discussion")),
        para(""),
        *(para(t, 1, 3) for t in ("LTVA Update", "Board Member Comments", "Adjournment")),
        para("Public Notice:"), para("The Agenda and Public Meeting Notice are posted on SouthTahoeTourism.com"),
    ]), SLTTID_LIST)


def test_word_list_numbering_is_rendered_into_the_text():
    [text] = docx_sections(slttid_agenda())
    lines = text.splitlines()
    assert lines[4:10] == ["A. Call to Order", "B. Roll Call", "C. Introduction of Guests and Staff",
                           "D. Approval of Minutes of Previous Meeting (Attachment)", "E. Approval of Agenda",
                           "F. Public Comment (Non-Agenda Items, 5-Minute Limit)"]
    # Level 3 has not been used before: it starts at its own "A."; level 5 restarts under each level-3 entry.
    assert "            A. Discussion/Possible Action" in lines
    assert "                    i. Treasures Report" in lines and "                    iii. 2026/2027 Budget Discussion" in lines
    assert lines[-5:-2] == ["            B. LTVA Update", "            C. Board Member Comments", "            D. Adjournment"]


def test_a_word_agenda_parses_with_its_layout():
    pages = [{"document_id": "doc", "page_number": n, "text": t} for n, t in enumerate(docx_sections(slttid_agenda()), 1)]
    result = parse_agenda("slttid", [{"id": "doc"}], pages)
    assert result.complete and not result.notes, result.warning
    assert [[i["source_number"], i["section"], i["title"]] for i in result.items][5:] == [
        ["F", None, "Public Comment (Non-Agenda Items, 5-Minute Limit)"],
        ["A.i", "Discussion/Possible Action", "Treasures Report"],
        ["A.ii", "Discussion/Possible Action", "Board Nomination Forms"],
        ["A.iii", "Discussion/Possible Action", "2026/2027 Budget Discussion"],
        ["B", "Agenda", "LTVA Update"], ["C", "Agenda", "Board Member Comments"], ["D", "Agenda", "Adjournment"]]


def test_multilevel_labels_overrides_restarts_and_style_numbering():
    defs = numbering(("0", [level(0, "decimal", "%1."), level(1, "decimal", "%1.%2"), level(2, "upperRoman", "(%3)", restart=0)]),
                     ("1", [level(0, "decimal", "Item %1:", start=5, legal=True)]),
                     nums=['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>',
                           '<w:num w:numId="2"><w:abstractNumId w:val="0"/>'
                           '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="10"/></w:lvlOverride></w:num>',
                           '<w:num w:numId="3"><w:abstractNumId w:val="1"/></w:num>'])
    styles = (f'<w:styles {NS}><w:style w:type="paragraph" w:styleId="Numbered"><w:pPr><w:numPr><w:numId w:val="3"/>'
              '</w:numPr></w:pPr></w:style><w:style w:type="paragraph" w:styleId="Numbered2"><w:basedOn w:val="Numbered"/></w:style></w:styles>')
    body = ''.join([para("One", 1, 0), para("One a", 1, 1), para("Note", 1, 2), para("Two", 1, 0), para("Two a", 1, 1),
                    para("Note", 1, 2), para("Ten", 2, 0), para("By style", style="Numbered"), para("Inherited", style="Numbered2"),
                    para("", 1, 0), para("Twelve", 1, 0)])
    [text] = docx_sections(docx(body, defs, styles))
    assert text.splitlines() == ["1. One", "    1.1 One a", "        (I) Note", "2. Two", "    2.1 Two a",
                                 "        (II) Note",  # lvlRestart 0: never restarts
                                 "10. Ten", "Item 5: By style", "Item 6: Inherited", "", "12. Twelve"]


def test_tables_page_breaks_and_hidden_text():
    body = ''.join([
        para("Cover"),
        '<w:p><w:r><w:t>Before</w:t><w:br w:type="page"/></w:r></w:p>',
        '<w:tbl><w:tr><w:tc>' + para("1.") + '</w:tc><w:tc>' + para("Budget hearing") + para("(action)") + '</w:tc></w:tr></w:tbl>',
        '<w:p><w:r><w:delText>deleted</w:delText><w:instrText>HYPERLINK</w:instrText><w:t>Kept</w:t><w:tab/><w:t>tab</w:t></w:r></w:p>',
        '<w:p><w:pPr><w:pageBreakBefore/></w:pPr><w:r><w:t>Last page</w:t></w:r></w:p>',
        '<w:sdt><w:sdtContent>' + para("In a content control") + '</w:sdtContent></w:sdt>',
    ])
    assert docx_sections(docx(body)) == ["Cover\nBefore", "1.   Budget hearing (action)\nKept tab", "Last page\nIn a content control"]


def test_number_formats():
    assert [format_number(n, "upperRoman") for n in (4, 9, 14)] == ["IV", "IX", "XIV"]
    assert [format_number(n, "lowerLetter") for n in (1, 26, 27)] == ["a", "z", "aa"]
    assert format_number(3, "decimalZero") == "03" and format_number(3, "bullet") == "•" and format_number(3, "ordinalText") == "3"


class Body(io.BytesIO):
    def __init__(self, data, content_type):
        super().__init__(data)
        self.headers = {"Content-Type": content_type}

    def geturl(self):
        return "https://example.gov/agenda.docx"


def test_capture_extracts_word_documents_by_file_type(monkeypatch):
    raw = slttid_agenda()
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: Body(raw, "application/octet-stream"))
    captured = source.download_extract("https://example.gov/TID%20Agenda%2010.29.2026.docx")
    assert captured["media_type"] == docx_source.MEDIA_TYPE and captured["page_count"] == 1
    assert captured["warnings"] == [docx_source.WARNING] and captured["bytes"] == len(raw)
    assert "A. Call to Order" in captured["pages"][0]["text"]
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: Body(b"PK\x03\x04 not a zip", "application/zip"))
    with pytest.raises(ValueError, match="not a readable Word document"):
        source.download_extract("https://example.gov/file.zip")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: Body(archive.getvalue(), "application/zip"))
    with pytest.raises(ValueError, match="not a Word document"):
        source.download_extract("https://example.gov/budget.xlsx")
