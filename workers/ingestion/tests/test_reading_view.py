"""The reading view of pdftotext -layout pages (no database)."""
from tahoe_ingest.reading_view import view, vocabulary

# A loan commitment form: a label column beside the body, labels right-aligned against it, and a label
# that wraps onto the next line inside the body's sentence (shortened from a Nevada water board report).
FORM = """Elk Point Country Club HOA
 Total Commitment      Recommendation
 $78,000.00            The Division recommends that the Board approve a loan commitment to
                       the Elk Point Country Club Homeowners Association.
 Amount                Resolution         Program and Terms

 System Infrastructure Elk Point Country Club HOA is supported by two wells, a 260,000-gallon water
                       storage tank, pump station, distribution system, and treatment system.

  DWSRF Priority Priority #21 on the Drinking Water SRF Priority List Effective May 2026.
           List
   System need The current treatment system for the Elk Point HOA is unable to sufficiently treat the high
  for the project uranium levels in the existing groundwater source in order to provide reliable and safe
                  drinking water. Resin exchanges in the current system are needed more often.
"""


def test_side_labels_leave_the_sentences_beside_them():
    text = view(FORM).text
    assert "The Division recommends that the Board approve a loan commitment to\nthe Elk Point" in text
    assert "System Infrastructure\nElk Point Country Club HOA is supported by two wells" in text
    # "for the project" was printed inside the sentence; it now follows it.
    assert ("The current treatment system for the Elk Point HOA is unable to sufficiently treat the high\nuranium levels "
            "in the existing groundwater source in order to provide reliable and safe\ndrinking water. Resin exchanges in the "
            "current system are needed more often.\nfor the project") in text
    assert "Elk Point Country Club HOA\n" in text  # a heading that runs into the body column stays whole


def test_every_character_comes_from_the_page_and_spans_map_back():
    page = view(FORM)
    for index, char in enumerate(page.text):
        if char != "\n":
            assert FORM[page.origin[index]] == char
    start = page.text.index("The current treatment")
    end = page.text.index("drinking water.") + len("drinking water.")
    first, last = page.span(start, end)
    quote = FORM[first:last]
    assert quote.startswith("The current treatment") and quote.endswith("drinking water.")
    assert "for the project" in quote  # the citation is the page's own text, label included


def test_ordinary_prose_tables_and_headings_are_left_as_printed():
    prose = ("                   2027-2031 Core Operating Principles Draft\n"
             "    Core Operating Principles are Board and Staff expectations that outline how TCPUD\n"
             "                    conducts its business in service to the community.\n")
    assert view(prose).text.split("\n")[1] == "Core Operating Principles are Board and Staff expectations that outline how TCPUD"
    table = ("Funding Source     Budget Thru      Actual         Total\n"
             "Total               $1,000,000     $12,015       $430,370\n"
             "                    Estimated      Estimated\n")
    assert [line.split()[0] for line in view(table).text.split("\n") if line] == ["Funding", "Total", "Estimated"]
    centred = ("Tahoe City Community Center Planning & Feasibility Study\n"
               "                     Proposed 1.5-Hour Community Meeting Format\n")
    assert view(centred).text.startswith("Tahoe City Community Center Planning & Feasibility Study\n")


def test_hyphenated_words_join_only_when_the_report_prints_them_whole():
    pages = ["The expansion adds microtransit service. Expanded micro-\ntransit zones start in July.",
             "A well-\nestablished program continues."]
    words = vocabulary(pages)
    assert "Expanded microtransit zones" in view(pages[0], words).text
    assert "well-\nestablished" in view(pages[1], words).text  # "wellestablished" is not printed anywhere
    joined = view(pages[0], words)
    start = joined.text.index("microtransit zones")
    first, last = joined.span(start, start + len("microtransit zones"))
    assert pages[0][first:last] == "micro-\ntransit zones"
