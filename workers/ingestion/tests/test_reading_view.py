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


# A form's label column as pdf_columns prints it: bodies at one column after a gutter of two or more spaces, the
# recipient's name above the block, and the page footer below it (shortened from a Nevada water board report).
GUTTERED = """Orovada General Improvement District
 Total Commitment      Recommendation
 $1,499,974.44         The Nevada Division of Environmental Protection (NDEP) recommends that the
                       Board for Financing Water Projects (Board) approve a loan commitment to Orovada
                       General Improvement District for a redundant tank and transmission line.
 Amount                Resolution         Program and Terms

     Project    The project includes the construction of a new 215,000-gallon storage tank and a
   Narrative    connecting main.
     Project    The Preliminary Engineering Report details three options, including no action; this project
Alternatives    was determined to be the best alternative.
   Evaluated
Page 1 of 6                             Orovada GID                                  May 2026
"""


def test_a_guttered_label_column_reads_label_first_even_beside_a_heading_and_footer():
    text = view(GUTTERED).text
    assert text.startswith("Orovada General Improvement District\nTotal Commitment\nRecommendation\n")
    # A line that continues the sentence with a capital ("General Improvement District") keeps the label beside
    # the next line ("Amount") waiting.
    assert ("Recommendation\n$1,499,974.44\nThe Nevada Division of Environmental Protection (NDEP) recommends that the\n"
            "Board for Financing Water Projects (Board) approve a loan commitment to Orovada\nGeneral Improvement District "
            "for a redundant tank and transmission line.\nAmount\n") in text
    assert "Project\nThe project includes the construction of a new 215,000-gallon storage tank and a\nconnecting main.\n" in text
    assert text.endswith("Evaluated\nPage 1 of 6                             Orovada GID                                  May 2026\n")


def test_memo_headers_and_lists_stay_in_rows():
    memo = ("TO:          Board of Directors                  DATE:   August 14, 2026\n"
            "FROM:        Kim Boyd                            SUBJ:   Lodge Project Update\n"
            "             Director of Strategic Affairs\n")
    assert view(memo).text.split("\n")[0] == "TO:          Board of Directors                  DATE:   August 14, 2026"
    bullets = ("•    For the Department Lands Pass Program, add Big Springs Ranch and remove Hope\n"
               "     Valley Wildlife Area, Crescent City Marsh WA, and the Eel River WA from the\n"
               "     program (see Exhibit 2 for the rationale for each property)\n"
               "•    Revise language to reflect existing federal regulations for seven refuges,\n"
               "     and reorganize information in consistent order across all nine refuges\n")
    assert view(bullets).text.split("\n")[0].startswith("•    For the Department")
    exhibits = ("A2.  Petition 2023-01, received January 19, 2023\n"
                "A3.  Petition 2026-06, received April 20, 2026\n"
                "A8.  Staff memo – Analysis and Recommendation for Petition 2026-11, dated August 7,\n"
                "     2026\n")
    assert view(exhibits).text.startswith("A2.  Petition 2023-01")


def test_a_table_of_wrapped_cells_reads_column_by_column():
    table = ("Requests the Commission add to an                The Act authorizes the Commission to establish\n"
             "agenda the status of the Fish and Game           a pilot program for issuing salvage permits,\n"
             "Code section, for discussion and to provide      contingent upon appropriation of funds. The\n"
             "a public explanation of the program.             requestor has been provided with a response.\n")
    assert view(table).text == ("Requests the Commission add to an\nagenda the status of the Fish and Game\nCode section, for "
                                "discussion and to provide\na public explanation of the program.\nThe Act authorizes the "
                                "Commission to establish\na pilot program for issuing salvage permits,\ncontingent upon "
                                "appropriation of funds. The\nrequestor has been provided with a response.\n")
    rows = ("Emerald Bay Shuttle Support                     $15,500\n"
            "FY 23-24 TTD Operations Support                 $1,148,135\n"
            "SSTMA Microtransit Operations – awarded FY 26   $750,000\n")
    assert view(rows).text == "\n".join(line.strip() for line in rows.split("\n"))
