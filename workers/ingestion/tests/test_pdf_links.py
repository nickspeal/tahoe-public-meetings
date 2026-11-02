"""Link annotations read from agenda PDFs: a "Zoom Link" whose URL the page text never prints."""
import io
import shutil

import pytest

from tahoe_ingest import http, source

pytestmark = pytest.mark.skipif(not (shutil.which("pdfinfo") and shutil.which("pdftotext")), reason="poppler-utils required")

ZOOM = "https://us02web.zoom.us/j/81234567890?pwd=abc123"
TEAMS = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0"


def pdf(pages: list[tuple[list[str], list[str]]]) -> bytes:
    """A minimal PDF: each page prints its lines and carries one URI link annotation per URL."""
    font = 3 * len(pages) + 3
    objects = {1: "<< /Type /Catalog /Pages 2 0 R >>",
               2: f"<< /Type /Pages /Kids [{' '.join(f'{3 + 3 * i} 0 R' for i in range(len(pages)))}] /Count {len(pages)} >>",
               font: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    for i, (lines, urls) in enumerate(pages):
        page, content, annots = 3 + 3 * i, 4 + 3 * i, 5 + 3 * i
        stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
        objects[page] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                         f"/Resources << /Font << /F1 {font} 0 R >> >> /Annots {annots} 0 R >>")
        objects[content] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
        objects[annots] = "[" + " ".join(f"<< /Type /Annot /Subtype /Link /Rect [72 {700 - 14 * n} 300 {714 - 14 * n}] "
                                         f"/Border [0 0 0] /A << /S /URI /URI ({url}) >> >>" for n, url in enumerate(urls)) + "]"
    out, offsets = b"%PDF-1.4\n", {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n{objects[number]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offsets[n]:010d} 00000 n \n".encode() for n in range(1, len(objects) + 1))
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


AGENDA = pdf([
    (["Board Meeting Agenda", "Remote option: Zoom Link"], [ZOOM, ZOOM, "mailto:clerk@example.gov"]),
    (["Page two"], ["https://example.gov/two"]),
    (["Page three"], []),
    (["Page four"], ["https://example.gov/four"]),  # neither an opening page nor the last
    (["Click here to join the Microsoft Teams meeting", "Adjournment"],
     [TEAMS, "https://www.microsoft.com/microsoft-teams/join-a-meeting"]),
])


class Body(io.BytesIO):
    headers = {"Content-Type": "application/pdf"}

    def geturl(self):
        return "https://example.gov/agenda.pdf"


def test_link_annotations_of_the_opening_and_last_pages_are_kept(monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: Body(AGENDA))
    captured = source.download_extract("https://example.gov/agenda.pdf")
    pages = {p["page_number"]: p for p in captured["pages"]}
    assert "zoom.us" not in pages[1]["text"]  # the text extraction drops the annotation's URL
    assert pages[1]["links"] == [ZOOM]  # repeats and non-web links are dropped
    assert pages[2]["links"] == ["https://example.gov/two"]
    assert "links" not in pages[3] and "links" not in pages[4]
    assert pages[5]["links"] == [TEAMS, "https://www.microsoft.com/microsoft-teams/join-a-meeting"]


def test_a_pdf_without_links_or_a_failed_listing_keeps_its_text(monkeypatch, tmp_path):
    plain = tmp_path / "plain.pdf"
    plain.write_bytes(pdf([(["Agenda"], [])]))
    assert source.link_annotations(str(plain), 1) == {}
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really")
    assert source.link_annotations(str(broken), 1) == {}
