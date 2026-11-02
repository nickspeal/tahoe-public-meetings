# 0030: Lake Calendar visual design

Status: accepted
Date: 2026-11-02
Related ideas: none

## Context
The site's first look (a warm off-white page, sage green accents, system sans with tight tracking, small-radius cards) read as the generic default style of AI-generated pages. The maintainer liked the layout, the simplicity, the system font, and the calendar-like date boxes, and wanted a visual identity without a drastic overhaul. Four directions were mocked up: a civic-record look with Public Sans and square corners; a serif "field guide" with cream and ochre; a "lake and granite" blue scheme with a display sans; and a dense "community calendar" with condensed headlines and highlighter yellow. None was adopted whole. The maintainer pulled elements from them one at a time.

## Decision
Confirmed by the maintainer on 2026-11-02, "Lake Calendar":
- Keep the layout and the system font stack. Use real 600 and 700 weights, and no uppercase apart from the TODAY label.
- Use a blue and white palette with several blues: page `#f4f7fa`, headings `#1d3f5e`, links, buttons, and the today band `#2b5f8c`, body ink `#14283c`, muted text `#4f6579`, lines `#d5e0ea`, tint `#e4eef6`, pill `#dbe8f3`, and date box border `#9fbdd6`. No dark hero bands.
- Round the corners: 10px for cards, 8px for fields and notes, 6px for tags.
- Turn date boxes into small calendar pages (month band, day numeral, weekday). Expected meetings get a dashed state and canceled meetings are grey.
- Reserve color for meaning. Routine changes (Revised, Rescheduled) and informational notes are blue. Only Canceled uses red. Amber is for notes that change what the reader should do, such as stale data.
- The brand mark is a calendar page with a wave inside it. The previous mountain mark resembled a ski resort logo.
- The link preview image keeps its composition and words, recolored into the blues and using the calendar mark. Its topic names are removed so it does not go stale as the topic catalog changes (decision 0008).

## Alternatives and consequences
- The civic-record look was too cold and square.
- The serif field guide with cream and ochre was still a familiar default look.
- The lake and granite scheme: the maintainer disliked the display typeface and found the dark hero too strong. Its blues were kept.
- The community calendar was too dense, and the uppercase headlines were disliked. Its calendar-page date boxes were kept.

The stylesheet is the single source of the palette. The redesign is design tokens plus small component changes, with no new components, so the layout and behavior are unchanged.

## Revisit when
- Reader feedback or tester sessions show the status colors are misread, for example a blue Revised pill being missed.
- The topic catalog or agency set grows enough that the preview image or the mark needs to carry more.
