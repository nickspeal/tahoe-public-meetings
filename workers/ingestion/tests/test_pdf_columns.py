"""Column alignment of pdftotext -layout text from word boxes (no database, no PDFs: trimmed text and boxes)."""
import json
import re
from pathlib import Path

from tahoe_ingest import staff_reports
from tahoe_ingest.pdf_columns import Box, align, bbox_pages
from tahoe_ingest.reading_view import view, vocabulary

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/pdf-columns"


def fixture(name):
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return data["text"], [Box(*box) for box in data["boxes"]]


def squeeze(text):
    return re.sub(r"\s+", "", text)


def stored(text):
    """As map-staff-reports keeps a page (``leading``): cleaned and stripped."""
    return staff_reports.clean_text(text).strip()


def test_a_label_column_run_into_its_body_is_set_apart():
    text, boxes = fixture("water-board-form")
    # pdftotext prints the right-aligned label one space before the body, and the body's own next line further right.
    assert "   System need The system currently operates" in text
    assert "                  operations within the next few years" in text
    aligned = align(text, boxes)
    assert squeeze(aligned) == squeeze(text)  # only spaces are added
    lines = aligned.split("\n")
    need = next(line for line in lines if "System need" in line)
    after = next(line for line in lines if line.lstrip().startswith("operations within"))
    assert need.index("The system") == after.index("operations") and "need  " in need
    county = next(line for line in lines if "County" in line)
    assert re.search(r"County {2,}Clark", county)


def test_the_form_reads_label_first_so_sentences_stay_whole():
    pages = [stored(align(*fixture("water-board-form"))), stored(align(*fixture("water-board-form-continued")))]
    words = vocabulary(pages)
    first, second = (view(page, words).text for page in pages)
    assert "System need\nThe system currently operates a SCADA system that is more than 20 years old. The\n" in first
    assert ("The Nevada Division of Environmental Protection (NDEP) recommends that the\nBoard for Financing Water "
            "Projects (Board) approve a Letter of Intent to fund Moapa\nValley Water District") in first
    assert "Data Acquisition (SCADA) improvements.\n$1,267,300.00" in first  # the value beside the sentence waits
    assert ("Project\nThe Moapa Valley Water District proposes to improve the existing SCADA system. The\nexisting system "
            "is dated") in second
    assert "38 different sites.\nNarrative" in second


def test_the_old_text_kept_its_labels_inside_the_sentences():
    text, _ = fixture("water-board-form-continued")
    assert "\nProject The Moapa Valley Water District proposes" in view(stored(text)).text


def test_a_table_whose_last_columns_ran_together_reads_column_by_column():
    text, boxes = fixture("request-table")
    assert "10/13-16/2026 specific funding" in text
    aligned = align(text, boxes)
    assert "10/13-16/2026  specific funding" in aligned
    page = view(stored(aligned)).text
    assert ("The Wildlife Traffic Safety Act authorizes the Commission to establish\na pilot program for issuing wildlife "
            "salvage permits, contigent upon\nappropriation of funds by the legislature. The program must have\nbeen "
            "established by January 1, 2022; that date passed with no\nspecific funding appropriated") in page
    assert "Requests the Commission add to an\nagenda the status of California Fish\nand Game Code Section 2000.6" in page


def boxes_for(rows):
    """Word boxes for rows of (y, [(x, text), ...]) in an 11-point font: a character is 5.5 points wide."""
    return [Box(x, y, x + 5.5 * len(word), y + 12.0, word) for y, words in rows for x, word in words]


def test_justified_text_bullets_and_colon_labels_are_left_as_printed():
    # Evenly stretched justified spaces, a sentence's wider space, a bullet, and "SUBJECT:" one space before its body.
    text = ("Venue status reports are attached for August 2026 (covering department\n"
            "• Medical coverage\n"
            "SUBJECT: Approve Accounts Paid and Payable\n"
            "         2026 to October 12, 2026")
    rows = [(100, [(36, "Venue"), (75, "status"), (118, "reports"), (167, "are"), (192, "attached"), (247, "for"),
                   (272, "August"), (316, "2026"), (348, "(covering"), (405, "department")]),
            (120, [(36, "•"), (48, "Medical"), (92, "coverage")]),
            (140, [(36, "SUBJECT:"), (90, "Approve"), (136, "Accounts"), (186, "Paid"), (213, "and"), (236, "Payable")]),
            (160, [(90, "2026"), (117, "to"), (132, "October"), (176, "12,"), (196, "2026")])]
    assert align(text, boxes_for(rows)) == text


def test_a_line_that_does_not_match_its_boxes_is_left_alone():
    text = "   System need The system works\n               fine today"
    rows = [(100, [(57, "System"), (99, "nede"), (136, "The"), (160, "system"), (200, "works")]),
            (112, [(136, "fine"), (163, "today")])]
    assert align(text, boxes_for(rows)) == text


def test_a_label_printed_on_its_own_line_between_body_rows_still_matches():
    # pdftotext prints "March 2015" on its own line; its box shares a row with the body line below it.
    text = ("              The Board reviewed the program in a series of meetings\n"
            "March 2015\n"
            "              analysis of the numbers and types of violations and\n"
            "April 2016 actions taken by the Board and its staff during the year")
    rows = [(100, [(110, "The"), (132, "Board"), (164, "reviewed"), (213, "the"), (235, "program"), (279, "in"), (295, "a"),
                   (306, "series"), (343, "of"), (359, "meetings")]),
            (112.5, [(36, "March"), (70, "2015")]),
            (113, [(110, "analysis"), (159, "of"), (175, "the"), (197, "numbers"), (241, "and"), (264, "types"), (297, "of"),
                   (313, "violations"), (373, "and")]),
            (126, [(36, "April"), (66, "2016"), (110, "actions"), (153, "taken"), (187, "by"), (203, "the"), (225, "Board"),
                   (258, "and"), (280, "its"), (299, "staff"), (332, "during"), (370, "the"), (392, "year")])]
    aligned = align(text, boxes_for(rows)).split("\n")
    assert aligned[3].index("actions") == aligned[2].index("analysis") == 14 and aligned[3].startswith("April 2016    actions")


def test_bbox_pages_reads_words_per_page():
    markup = ('<doc><page width="612" height="792">\n<word xMin="36.0" yMin="10.5" xMax="60.25" yMax="22.0">R&amp;D</word>\n'
              '</page>\n<page width="612" height="792">\n</page></doc>')
    assert bbox_pages(markup) == [[Box(36.0, 10.5, 60.25, 22.0, "R&D")], []]


def test_read_pdf_aligns_columns_unless_asked_not_to(monkeypatch, tmp_path):
    text, boxes = fixture("water-board-form")
    markup = "<doc><page>" + "".join(f'<word xMin="{b.x0}" yMin="{b.y0}" xMax="{b.x1}" yMax="{b.y1}">{b.text}</word>'
                                       for b in boxes) + "</page></doc>"
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7")

    class Fetched:
        sha256 = "abc"

        def __init__(self):
            self.path = path

        def head(self, n):
            return path.read_bytes()[:n]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Done:
        def __init__(self, out):
            self.stdout = out

    calls = []

    def run(args, **kwargs):
        calls.append(args[:2])
        if args[0] == "pdfinfo":
            return Done("Pages:          1\n")
        out = markup if "-bbox" in args else text + "\f"
        return Done(out.encode() if not kwargs.get("text") else out)

    monkeypatch.setattr(staff_reports.http, "download", lambda url, **kw: Fetched())
    monkeypatch.setattr(staff_reports.subprocess, "run", run)
    page = staff_reports.read_pdf("https://example.gov/report.pdf")["pages"][0]["text"]
    assert page == align(text, boxes) != text and ["pdftotext", "-bbox"] in calls
    calls.clear()
    assert staff_reports.read_pdf("https://example.gov/report.pdf", columns=False)["pages"][0]["text"] == text
    assert ["pdftotext", "-bbox"] not in calls
