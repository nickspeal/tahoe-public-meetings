"""Agency agenda layouts for the deterministic outline parser.

Each entry was written against that agency's published agendas; the tests in
``tests/test_agenda_layouts.py`` replay real agenda text for every layout.
Add a layout here when a new agency (or a new format from an existing agency)
needs item extraction, then add its agenda text to the test fixtures.
"""
from __future__ import annotations

from .outline import LOWER, NUM, NUM_LOOSE, ROMAN_LEVEL, UPPER, Appendix, Layout, Level

# A wrapped title stops at a staff attribution such as "Matt Click, Executive Director".
STAFF_LINE = r"^[A-Z][\w.'’-]+(?: [A-Z][\w.'’-]+){1,3},\s+(?:and\s+)?[A-Z]"
# Unnumbered ALL-CAPS section headings ("CONSENT CALENDAR", "ACTION ITEMS:").
CAPS_HEADING = r"^[A-Z][A-Z0-9 &/,'’()\-–]{3,}:?$"
ACTION_TAGS = (r"(?i)\s*-?\s*\[(?:non-action item|for possible action)\]", "")
PAREN_DESCRIPTION = (r"^([^a-z]+?)\s+\((?=[A-Z][a-z]).*$", r"\1")  # "COMMENTS FROM THE PUBLIC (This is an..." -> heading

SLT_HEADINGS = r"^(?:New Business|Unfinished Business|Old Business|Consent(?: Agenda)?|Presentations?|Public Hearings?)$"
FOR_ACTION = (r"(?i)^For (?:possible action|presentation only|discussion only|information only)[.:]\s*", "")

LEGISTAR_FILE = Level("num", r"(?P<v>\d{1,3})\.\s+\d{2}-\d{3,5}(?=\s|$)")
# Douglas County children: "A." letters, or dotted "11.1" (whose "11." parent may be unprinted).
DOUGLAS_CHILD = Level("free", r"(?:(?P<p>\d{1,2})\.)?(?P<v>(?<=\.)\d{1,2}|[A-Z](?=\.))\.?(?=\s)")
# TRPA tables: "A   Title   Staff   Purpose"; a single space is accepted only on a table row.
UPPER_BARE = Level("upper", r"(?P<v>[A-Z])(?=\s{2,}\S|\s\S.*\S\s{3,}\S)")
UPPER_ANY = Level("upper", r"\(?(?P<v>[A-Z])[.)](?=\s|$)")
NUM_TIGHT = Level("num", r"(?P<v>\d{1,2})\.(?=\s|[A-Z])")
LOWER_ROMAN = Level("lroman", r"(?P<v>[ivx]{1,4})\.(?=\s)", item=False)

LAYOUTS: dict[str, tuple[Layout, ...]] = {
    "california-fish-game": (
        Layout("california-fish-game", "Fish and Game Commission agenda",
               cover=(r"Fish and Game Commission", r"Meeting Agenda"),
               levels=(NUM,), first_pages=30, wrap="lowercase", max_title_lines=3, restart=True,
               min_items=5),
    ),
    "california-state-parks": (
        Layout("california-state-parks", "OHMVR Commission agenda",
               cover=(r"OFF-HIGHWAY MOTOR VEHICLE RECREATION COMMISSION", r"AGENDA"),
               levels=(ROMAN_LEVEL, LOWER), first_pages=3, max_title_lines=3),
        Layout("california-state-parks", "State Park and Recreation Commission agenda",
               cover=(r"STATE PARK AND RECREATION COMMISSION", r"AGENDA"),
               levels=(NUM_TIGHT, LOWER, LOWER_ROMAN), first_pages=3, max_title_lines=2,
               skip=r"^State Park and Recreation Commission Meeting Agenda"),
    ),
    "douglas-commissioners": (
        Layout("douglas-commissioners", "Douglas County Board of Commissioners agenda",
               cover=(r"Douglas County", r"Board of County", r"(?i)agenda"),
               levels=(NUM, DOUGLAS_CHILD), first_pages=12, max_title_lines=3, indent_tolerance=99, headings=CAPS_HEADING,
               title_end=r"\.$", title_cleanup=(FOR_ACTION,),
               skip=r"^Douglas County Board of County Commissioners\s+\w+ \d{1,2}, \d{4}$|^\d{1,3}$|^LUNCH BREAK$|^FINAL AGENDA$"),
    ),
    "douglas-planning": (
        Layout("douglas-planning", "Douglas County Planning Commission agenda",
               cover=(r"Douglas County", r"Planning Commission", r"(?i)agenda"),
               levels=(NUM, UPPER), first_pages=8, max_title_lines=3, indent_tolerance=99, headings=CAPS_HEADING,
               title_end=r"\.$", title_cleanup=(FOR_ACTION,), min_items=2,
               skip=r"^Douglas County Planning Commission\s+\w+ \d{1,2}, \d{4}$|^\d{1,3}$"),
    ),
    "douglas-transportation": (
        Layout("douglas-transportation", "Douglas County joint Board and RTC agenda",
               cover=(r"Douglas County", r"(?i)agenda"),
               levels=(NUM, DOUGLAS_CHILD), first_pages=12, max_title_lines=3, indent_tolerance=99, headings=CAPS_HEADING,
               title_end=r"\.$", title_cleanup=(FOR_ACTION,),
               skip=r"^Douglas County .*\s+\w+ \d{1,2}, \d{4}$|^\d{1,3}$|^LUNCH BREAK$|^FINAL AGENDA$"),
    ),
    "el-dorado-planning": (
        Layout("el-dorado-planning", "El Dorado County Legistar agenda",
               cover=(r"County of El Dorado", r"MEETING AGENDA"),
               levels=(LEGISTAR_FILE,), first_pages=20, max_title_lines=8, headings=CAPS_HEADING,
               min_items=1, skip=r"^County of El Dorado\s+Page \d+|Printed on \d"),
    ),
    "el-dorado-supervisors": (
        Layout("el-dorado-supervisors", "El Dorado County Legistar agenda",
               cover=(r"County of El Dorado", r"(?i)agenda"),
               levels=(LEGISTAR_FILE,), first_pages=40, max_title_lines=8, headings=CAPS_HEADING,
               skip=r"^County of El Dorado\s+Page \d+|Printed on \d"),
    ),
    "ivgid": (
        Layout("ivgid", "IVGID Board of Trustees agenda",
               cover=(r"Incline Village General Improvement District", r"PLEASE TAKE NOTICE"),
               levels=(UPPER, NUM, Level("upper", UPPER.pattern, item=False)), first_pages=8,
               max_title_lines=6, stop=r"^(?:Recommendation for Action|Public Comments will be taken)",
               skip=(r"^Incline Village General Improvement District|^recreation programs and facilities|"
                     r"^893 Southwood Boulevard|^www\.yourtahoeplace\.com|^Agenda for the Board Meeting of"),
               title_cleanup=((r"^SUBJECT:\s*", ""), (r"^\((?:For|Not for) Possible Action\)\s*-?\s*", ""),
                              (r"^PUBLIC HEARING\s*-\s*Time:.*?practicable\.\s*", ""),
                              (r"(?i)\s*-?\s*\((?:For|Not for) possible Action\)", ""),
                              (r"\s*\(Requesting Staff Member:.*$", ""), (r"\*$", "")),
               caps_lead=True),
    ),
    "ndep": (
        Layout("ndep", "Nevada Division of Environmental Protection board agenda",
               cover=(r"STATE OF NEVADA", r"AGENDA"),
               levels=(NUM_LOOSE,), first_pages=4, wrap="lowercase", max_title_lines=2, max_gap=1,
               skip=r"^Page \d+ of \d+"),
    ),
    "nevada-state-parks": (
        Layout("nevada-state-parks", "Nevada Division of State Parks meeting agenda",
               cover=(r"(?i)state parks", r"AGENDA"),
               levels=(NUM,), first_pages=4, max_title_lines=2, wrap="lowercase"),
    ),
    "ntpud": (
        Layout("ntpud", "NTPUD Board agenda",
               cover=(r"NORTH TAHOE PUBLIC UTILITY DISTRICT", r"(?i)agenda"),
               levels=(UPPER, NUM, Level("lower", LOWER.pattern, item=False)), first_pages=5,
               max_title_lines=6, skip=r"^Agenda Posted on", caps_colon=True, caps_lead=True),
    ),
    "placer-planning": (
        Layout("placer-planning", "Placer County Planning Commission agenda",
               cover=(r"COUNTY OF PLACER", r"PLANNING COMMISSION AGENDA"),
               levels=(NUM, UPPER, Level("num", NUM.pattern, item=False), Level("lower", LOWER.pattern, item=False)),
               first_pages=8, max_title_lines=3, stop=r"^(?:Project Location|Assessor Parcel|County Staff)",
               skip=r"^PLANNING COMMISSION\s+PAGE \d+|^[A-Z]+ \d{1,2}, \d{4}$"),
    ),
    "placer-supervisors": (
        Layout("placer-supervisors", "Placer County Board of Supervisors agenda",
               cover=(r"COUNTY OF PLACER", r"BOARD OF SUPERVISORS"),
               levels=(NUM, UPPER_ANY, Level("num", NUM.pattern, item=False),
                       Level("lower", r"\(?(?P<v>[a-z])[.)](?=\s)", item=False)),
               first_pages=14, max_title_lines=3, prefix=r"^\s{0,2}\d{1,3}(?![.\d:])(?=\s+\S)",
               stop=r"\d+ min\)$|^\(",
               skip=r"^BOARD OF SUPERVISORS\s+PAGE \d+|^[A-Z]+ \d{1,2}, \d{4}$"),
    ),
    "placer-transportation": (
        Layout("placer-transportation", "PCTPA Board agenda",
               cover=(r"PLACER COUNTY TRANSPORTATION PLANNING AGENCY", r"A\s*G\s*E\s*N\s*D\s*A"),
               levels=(UPPER, NUM, Level("lower", LOWER.pattern, item=False)), first_pages=4,
               max_title_lines=3, end=r"^Next Board Meeting", end_item=False,
               suffix=r"\s{3,}(?:Action|Info|Separate|cover|Pg\.\s*\d+)(?:\s+Pg\.\s*\d+)?\s*$",
               stop=STAFF_LINE + r"|^(?:These items|Persons may|Adopt a resolution|Receive)",
               skip=r"^BOARD OF DIRECTORS MEETING AGENDA$|^Page \d+$|^[A-Z][a-z]+ \d{1,2}, \d{4}$|"
                    r"^PLACER COUNTY TRANSPORTATION PLANNING AGENCY$|^2260 Douglas|^www\.pctpa\.net"),
    ),
    "slt-parks-recreation-commission": (
        Layout("slt-parks-recreation-commission", "South Lake Tahoe Parks and Recreation Commission agenda",
               cover=(r"City of South Lake Tahoe", r"Parks and Recreation Commission", r"Meeting Agenda"),
               levels=(NUM,), first_pages=6, max_title_lines=2,
               stop=r"^(?:Requested Action|Responsible Staff|\S.*\.(?:docx?|pdf)$)",
               headings=SLT_HEADINGS,
               heading_items=(r"^(?:Call to Order|Roll Call|Public Communications?|Updates and Announcements|"
                              r"Commissioner Reports|Commissioner Assignments)")),
    ),
    "slt-planning": (
        Layout("slt-planning", "South Lake Tahoe Planning Commission agenda",
               cover=(r"City of South Lake Tahoe", r"Planning Commission"),
               levels=(NUM,), first_pages=6, max_title_lines=2,
               stop=r"^(?:Requested Action|Responsible Staff|\S.*\.(?:docx?|pdf)$)",
               headings=SLT_HEADINGS,
               heading_items=(r"^(?:Call to Order|Roll Call|Public Communications?|Updates and Announcements|"
                              r"Planning Commissioner Reports|Commissioner Reports)")),
    ),
    "stpud": (
        Layout("stpud", "South Tahoe PUD Board agenda",
               cover=(r"SOUTH TAHOE PUBLIC UTILITY DISTRICT", r"BOARD MEETING AGENDA"),
               levels=(NUM_LOOSE, LOWER), first_pages=6, max_title_lines=3, max_gap=1, stop=r"^\(",
               skip=r"BOARD MEETING AGENDA\s+[–-]\s+\w+ \d{1,2}, \d{4}\s+PAGE",
               title_cleanup=(PAREN_DESCRIPTION,),
               after_end=(Appendix(r"^ITEMS FOR CONSENT$", LOWER, parent="4"),)),
    ),
    "tcpud": (
        Layout("tcpud", "TCPUD Board agenda",
               cover=(r"TAHOE CITY PUBLIC UTILITY DISTRICT", r"Board of Directors"),
               levels=(UPPER, NUM, Level("lower", LOWER.pattern, item=False)), first_pages=2,
               wrap="none", caps_lead=True),
    ),
    "trcd": (
        Layout("trcd", "Tahoe RCD Board agenda",
               cover=(r"TAHOE RESOURCE CONSERVATION DISTRICT", r"AGENDA"),
               levels=(ROMAN_LEVEL, NUM), first_pages=4, max_title_lines=4, stop=r"^[•*]",
               suffix=(r"\s{2,}(?:Action/Approve|Discussion Only|Action|Information(?:al)?(?: Only)?)"
                       r"(?:\s+[\dx][\d\-–x, ]*)?\s*$|\s{4,}[\dx]+(?:\s*[-–]\s*\d+)?\s*$"),
               skip=r"^RECOMMENDATION\s+PAGE$", title_cleanup=(PAREN_DESCRIPTION,)),
    ),
    "trpa-advisory": (
        Layout("trpa-advisory", "TRPA Advisory Planning Commission agenda",
               cover=(r"Tahoe Regional Planning Agency", r"Advisory Planning Commission"),
               levels=(NUM, UPPER_BARE), first_pages=4, max_title_lines=3,
               column_header=r"^\s*Item\s{2,}(?P<col>\S)", column_gap=True, skip=r"AGENDA ITEM NO\.|^\d{1,3}$"),
    ),
    "trpa-governing": (
        Layout("trpa-governing", "TRPA Governing Board agenda",
               cover=(r"Tahoe Regional Planning Agency", r"Governing Board"),
               levels=(NUM, UPPER_BARE), first_pages=16, max_title_lines=3, start=r"^TRPA GOVERNING BOARD MEETING$",
               column_header=r"^\s*Item\s{2,}(?P<col>\S)", column_gap=True, skip=r"AGENDA ITEM NO\.|^\d{1,3}$",
               after_end=(Appendix(r"^(?P<parent>\d+)\.\s+TRPA CONSENT CALENDAR$", UPPER_BARE,
                                   end=r"^Details:|^AGENDA$|^\d+\.\s+TMPO CONSENT CALENDAR"),
                          Appendix(r"^(?P<parent>\d+)\.\s+TMPO CONSENT CALENDAR$", UPPER_BARE,
                                   end=r"^Details:|^AGENDA$|^\d+\.\s+TRPA CONSENT CALENDAR"))),
    ),
    "truckee-north-shore-tma": (
        Layout("truckee-north-shore-tma", "TNT/TMA Board agenda",
               cover=(r"TRUCKEE NORTH TAHOE TRANSPORTATION MANAGEMENT ASSOCIATION", r"BOARD MEETING AGENDA"),
               levels=(NUM,), first_pages=3, wrap="any", max_title_lines=2, section_restart=True, indent_tolerance=99,
               headings=CAPS_HEADING, suffix=r"\s+\d{1,2}:\d{2}\s*[ap]m\s*$", stop=STAFF_LINE + r"|^-"),
    ),
    "truckee-tahoe-airport": (
        Layout("truckee-tahoe-airport", "Truckee Tahoe Airport District agenda",
               cover=(r"TRUCKEE TAHOE AIRPORT DISTRICT", r"MEETING AGENDA"),
               levels=(NUM,), first_pages=4, max_title_lines=2, headings=CAPS_HEADING, min_items=2,
               suffix=r"\s*-{3,}\s*TAB\s+\d+\s*$", stop=r"^\d+-"),
    ),
    "washoe-commissioners": (
        Layout("washoe-commissioners", "Washoe County Board of Commissioners agenda",
               cover=(r"(?i)washoe county", r"(?i)board of county commissioners"),
               levels=(NUM, Level("free", r"(?P<p>\d{1,2})\.(?P<v>[A-Z]\.\d{1,2})\.(?=\s)")), first_pages=40,
               max_title_lines=3, title_end=r"\.$|\)$",
               title_cleanup=(ACTION_TAGS, (r"^(Public Comment)\.\s.*$", r"\1"))),
    ),
    "washoe-planning": (
        Layout("washoe-planning", "Washoe County Planning Commission agenda",
               cover=(r"(?i)washoe county", r"(?i)planning commission"),
               levels=(NUM, UPPER), first_pages=12, max_title_lines=3, title_cleanup=(ACTION_TAGS,),
               title_end=r"(?i)\[(?:non-action item|for possible action)\]$"),
    ),
}
