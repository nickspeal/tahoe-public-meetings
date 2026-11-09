import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

/**
 * The public export the web tests build the site from (scripts/build-test-site.mjs).
 *
 * Every record is made up, and every date is relative to the day the tests run, so a meeting that is
 * upcoming today is upcoming whenever the tests run. Each record exists for the cases the tests name
 * next to it; tests look records up by these IDs. The shape follows packages/domain/public.ts and
 * passes scripts/check_export.py, which the build script runs on it.
 */

const TZ = 'America/Los_Angeles';
const registry = JSON.parse(readFileSync(new URL('../../../../packages/domain/agencies.json', import.meta.url), 'utf8'));
const catalog = JSON.parse(readFileSync(new URL('../../../../packages/domain/topics.json', import.meta.url), 'utf8'));

const sha = (key) => createHash('sha256').update(key).digest('hex');
const pacificDay = (date) => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
const addDays = (key, days) => new Date(Date.parse(`${key}T12:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);

/** The UTC instant of a Pacific wall-clock time, e.g. ('2026-11-17', '17:30') → '2026-11-18T01:30:00Z'. */
function pacificInstant(key, time) {
  const probe = new Date(`${key}T${time}:00Z`);
  const offset = new Intl.DateTimeFormat('en-US', { timeZone: TZ, timeZoneName: 'longOffset' })
    .formatToParts(probe).find((part) => part.type === 'timeZoneName').value.replace('GMT', '');
  return new Date(`${key}T${time}:00${offset}`).toISOString().replace('.000Z', 'Z');
}

/** Registry fields as the worker exports them: `extends` supplies shortName and URLs, never name or coverageNote. */
function exportedAgency(id, fallbackUrl) {
  const entry = registry.find((candidate) => candidate.id === id);
  const parent = entry.extends ? registry.find((candidate) => candidate.id === entry.extends) : {};
  const pick = (key) => entry[key] ?? parent[key] ?? null;
  return {
    id, name: entry.name, shortName: pick('shortName') ?? entry.name, officialUrl: pick('officialUrl') ?? fallbackUrl, sourceUrl: pick('sourceUrl') ?? fallbackUrl,
    lastCheckedAt: null, status: 'ok', error: null, ...(entry.coverageNote ? { coverageNote: entry.coverageNote } : {}),
  };
}

export function buildFixture(now = new Date()) {
  const today = pacificDay(now);
  const day = (offset) => addDays(today, offset);
  const at = (offset, time) => pacificInstant(day(offset), time);
  const generatedAt = now.toISOString();
  const checked = at(-1, '06:00');

  /** An agenda document whose first page prints each item, and the items citing it. */
  function agenda(meetingId, url, fetchedOffset, rows, { title = 'Agenda', pageCount = 12 } = {}) {
    const docId = `${meetingId}:agenda:${sha(url).slice(0, 16)}`;
    const fetchedAt = at(fetchedOffset, '08:15');
    const text = rows.map((row) => `${row.number ? `${row.number}. ` : ''}${row.title}`).join('\n');
    const document = { id: docId, title, url, sha256: sha(url), fetchedAt, mediaType: 'application/pdf', pageCount, status: 'extracted', warnings: [], pages: [{ number: 1, text }] };
    const items = rows.map((row, index) => {
      const citations = row.placeholder ? [] : [{ documentId: docId, page: 1, quote: row.title }];
      return {
        id: `${meetingId}:${index + 1}`, number: row.number ?? '', order: index + 1, title: row.title, section: row.section ?? null, topics: row.topics ?? [],
        summary: row.placeholder ? null : { text: `Agenda excerpt: “${row.title}”`, status: 'published', method: 'deterministic-source-excerpt-v1', generatedAt: fetchedAt, citations },
        citations, change: 'new', updatedAt: fetchedAt, suppressed: false, timeCertain: null,
      };
    });
    return { document, items, history: [{ at: fetchedAt, change: 'added', document: title }] };
  }

  /** A plain document (minutes, a notice) with no agenda items. */
  function document(meetingId, title, url, fetchedOffset) {
    const fetchedAt = at(fetchedOffset, '08:15');
    return { id: `${meetingId}:doc:${sha(url).slice(0, 16)}`, title, url, sha256: sha(url), fetchedAt, mediaType: 'application/pdf', pageCount: 3, status: 'extracted', warnings: [], pages: [{ number: 1, text: title }] };
  }

  function meeting({ id, title, offset, time = null, documents = [], items = [], history = [], ...fields }) {
    const [agencyId, sourceId] = [id.slice(0, id.indexOf(':')), id.slice(id.indexOf(':') + 1)];
    return {
      id, agencyId, sourceId, host: null, kind: 'confirmed', title, topics: [],
      startsAt: time ? at(offset, time) : null, date: day(offset), timezone: TZ, status: 'scheduled', location: null,
      officialUrl: `https://${agencyId}.example.org/meetings/${sourceId}`,
      participation: { instructions: null, url: null, remoteUrl: null, deadline: null, watchUrl: null, phone: null, email: null, fromAgenda: false },
      minutesUrl: null, minutesStatus: null, minutesNote: null, recordingUrl: null, lastCheckedAt: checked, updatedAt: checked, change: 'new',
      warnings: [], documents, history, items, duration: { minutes: 120, source: 'estimated', basis: 'Using a typical length of 2 h for this body.' },
      ...fields,
    };
  }

  /** A meeting with a captured agenda: `rows` are its items in agenda order. */
  function withAgenda(fields, rows, options) {
    const url = `https://${fields.id.split(':')[0]}.example.org/meetings/${fields.id.split(':')[1]}/agenda.pdf`;
    const captured = agenda(fields.id, url, fields.offset - 6, rows, options);
    return meeting({ documents: [captured.document], items: captured.items, history: captured.history, ...fields });
  }

  const stored = [
    // The richest page: numbered and unnumbered items, routine items left out, a placeholder line, citations,
    // a revised agenda in its history, and a biking item for the ?topic=biking&agency=slt timeline.
    (() => {
      const record = withAgenda({ id: 'slt:2001', title: 'City Council Regular Meeting', offset: -30, time: '17:30', location: 'City Hall Council Chambers, 1901 Lisa Maloff Way' }, [
        { title: 'Call to Order' },
        { title: 'Roll Call' },
        { title: 'Pledge of Allegiance' },
        { title: 'Public Comment' },
        { number: '1', title: 'Approval of Minutes' },
        { number: '2', title: 'Prioritization of Bikes at City Intersections and Bike Lanes', topics: ['biking'] },
        { number: '3', title: 'Housing Element Annual Progress Report', topics: ['housing'] },
        { number: '4', title: 'Not Found In Agenda', placeholder: true },
        { number: '5', title: 'Vacation Home Rental Ordinance Amendments', topics: ['housing', 'short-term-rentals'] },
      ]);
      record.history.push({ at: at(-33, '09:40'), change: 'revised', document: 'Agenda' });
      return { ...record, minutesUrl: 'https://slt.example.org/meetings/2001/minutes.pdf', minutesStatus: 'approved', recordingUrl: 'https://slt.example.org/meetings/2001/video' };
    })(),
    // Two named sections, with an uncategorized item inside one.
    withAgenda({ id: 'slt:2002', title: 'City Council Regular Meeting', offset: -9, time: '17:30' }, [
      { number: '1', title: 'Approval of Warrants', section: 'Consent Calendar' },
      { number: '2', title: 'Award of Contract for Al Tahoe Boulevard Bike Lane Striping', section: 'Consent Calendar', topics: ['biking'] },
      { number: '3', title: 'Transit Center Lease Renewal', section: 'Consent Calendar', topics: ['transit'] },
      { number: '4', title: 'Workforce Housing Incentive Program', section: 'New Business', topics: ['housing'] },
      { number: '5', title: 'Snow Removal Equipment Purchase', section: 'New Business' },
    ]),
    // Upcoming, timed, agenda posted: shown by default, with a calendar menu; the body page's next meeting.
    withAgenda({ id: 'slt:2003', title: 'City Council Regular Meeting', offset: 4, time: '17:30', location: 'City Hall Council Chambers, 1901 Lisa Maloff Way' }, [
      { number: '1', title: 'Lake Tahoe Boulevard Transit Stop Improvements', topics: ['transit', 'biking'] },
      { number: '2', title: 'Accessory Dwelling Unit Fee Waiver', topics: ['housing'] },
    ]),
    // Planned from the agenda planning calendar: five previewed subjects, a calendar file that says "Subject to change."
    meeting({
      id: `slt:planned-${day(25)}`, title: 'City Council Meeting', offset: 25, time: '17:30', planned: true, topics: ['biking', 'housing'],
      officialUrl: 'https://slt.example.org/agenda-planning-calendar.pdf',
      previews: [
        ['Prioritization of Bikes at City Intersections and Bike Lanes', ['biking'], 2],
        ['Housing Element Update', ['housing'], 2],
        ['Fire Station 3 Remodel', [], 3],
        ['Bijou Bike Park Expansion', ['biking'], 3],
        ['Parking Citation Fee Schedule', [], 4],
      ].map(([subject, topics, page]) => ({
        subject, section: null, topics,
        source: { kind: 'planning-calendar', title: 'Agenda Planning Calendar', url: 'https://slt.example.org/agenda-planning-calendar.pdf', publishedOn: day(-6), quote: subject, page },
      })),
    }),

    // A transit and biking meeting for ?topic=transit&agency=ttd and /topics/biking/?agency=ttd; one item's
    // topics are stored out of catalog order.
    withAgenda({ id: 'ttd:1001', title: 'TTD Board of Directors Meeting', offset: -40, time: '15:00' }, [
      { number: '1', title: 'Fleet Electrification Plan', topics: ['environment', 'transit'] },
      { number: '2', title: 'US 50 Bike Path Maintenance Agreement', topics: ['biking'] },
    ]),
    // The agenda topic filter: items in transit, biking, and housing, and one uncategorized.
    withAgenda({ id: 'ttd:1002', title: 'TTD Board of Directors Meeting', offset: -12, time: '15:00' }, [
      { number: '1', title: 'Route 50 Service Change', topics: ['transit'] },
      { number: '2', title: 'Stateline Bike Share Pilot', topics: ['biking'] },
      { number: '3', title: 'Employee Housing Lease', topics: ['housing'] },
      { number: '4', title: 'Audit Committee Appointments' },
    ]),
    // Upcoming with an agenda, in transit and environment.
    withAgenda({ id: 'ttd:1003', title: 'TTD Board of Directors Meeting', offset: 6, time: '15:00' }, [
      { number: '1', title: 'Zero-Emission Bus Procurement', topics: ['transit', 'environment'] },
    ]),
    // Upcoming, no agenda yet: behind the reveal with the expected pill.
    meeting({ id: 'ttd:1004', title: 'TTD Board of Directors Meeting', offset: 20, time: '15:00', location: 'Stateline Transit Center' }),

    // Canceled, timed, in the past: no calendar event or file.
    meeting({ id: 'truckee:3001', title: 'Truckee Town Council Meeting', offset: -20, time: '17:00', status: 'canceled', change: 'canceled' }),
    // Upcoming with an agenda: the card's date badge and item count.
    withAgenda({ id: 'truckee:3002', title: 'Truckee Town Council Meeting', offset: 8, time: '17:00' }, [
      { number: '1', title: 'Brockway Road Trail Design Contract', topics: ['biking'] },
      { number: '2', title: 'Short-Term Rental Program Annual Report', topics: ['housing', 'short-term-rentals'] },
      { number: '3', title: 'Adjournment' },
    ]),
    // Upcoming, date only, no agenda and nothing found yet: no calendar menu, "No agenda found yet".
    meeting({ id: 'truckee:3003', title: 'Truckee Town Council Meeting', offset: 13 }),

    // Agenda captured but no items listed: "Open the official agenda".
    meeting({ id: 'ivgid:5001', title: 'IVGID Board of Trustees Regular Meeting', offset: -25, time: '18:00',
      documents: [document('ivgid:5001', 'Agenda Packet', 'https://ivgid.example.org/meetings/5001/packet.pdf', -31)],
      history: [{ at: at(-31, '08:15'), change: 'added', document: 'Agenda Packet' }] }),
    // Only minutes captured: the agenda is elsewhere.
    meeting({ id: 'south-shore-tma:5101', title: 'South Shore TMA Board of Directors Meeting', offset: -5, time: '08:30',
      documents: [document('south-shore-tma:5101', 'Minutes', 'https://south-shore-tma.example.org/meetings/5101/minutes.pdf', -2)],
      history: [{ at: at(-2, '08:15'), change: 'added', document: 'Minutes' }] }),
    // Past with no documents: the official meeting page has the agenda.
    meeting({ id: 'tcpud:5201', title: 'TCPUD Board of Directors Meeting', offset: -50, time: '08:30' }),
    // A notice project missing from the posted agenda.
    { ...withAgenda({ id: 'douglas-planning:5301', title: 'Douglas County Planning Commission Meeting', offset: -3, time: '13:00' }, [
      { number: '1', title: 'Master Plan Amendment for the Clear Creek Area', topics: ['housing'] },
    ]), previewNotOnAgenda: ['Genoa Lakes Village'] },

    // TRPA: a past Governing Board agenda, and an APC meeting planned from a notice (date only).
    withAgenda({ id: 'trpa-governing:4001', title: 'TRPA Governing Board Meeting', offset: -15, time: '09:30' }, [
      { number: 'VI.A', title: 'Regional Transportation Plan Amendment', topics: ['transit'] },
      { number: 'VI.B', title: 'Shoreline Plan Implementation Report', topics: ['environment'] },
    ]),
    meeting({
      id: `trpa-advisory:planned-${day(16)}`, title: 'TRPA Advisory Planning Commission Meeting', offset: 16, planned: true, topics: ['biking', 'environment'],
      officialUrl: 'https://trpa.example.org/west-shore-trail-nop.pdf',
      previews: [{ subject: 'West Shore Trail – Meeks Bay to D.L. Bliss State Park scoping', section: 'Public hearing', topics: ['biking', 'environment'],
        source: { kind: 'notice', title: 'West Shore Trail Notice of Preparation', url: 'https://trpa.example.org/west-shore-trail-nop.pdf', publishedOn: day(-8), quote: 'Advisory Planning Commission scoping meeting', page: 1 } }],
    }),

    // Other: one-off meetings that name their hosts; the upcoming one has only its notice.
    meeting({ id: `other:one-off-west-shore-trail-scoping-${day(7)}`, title: 'West Shore Trail Scoping Meeting', offset: 7, time: '17:30', host: 'TRPA and El Dorado County',
      location: 'Meeks Bay Fire Station', officialUrl: 'https://trpa.example.org/west-shore-trail-nop.pdf',
      duration: { minutes: 90, source: 'published', basis: 'The notice gives 5:30 to 7:00 PM.' } }),
    meeting({ id: `other:one-off-placer-trails-open-house-${day(-18)}`, title: 'Tahoe Basin Trails Open House', offset: -18, time: '16:00', host: 'Placer County',
      officialUrl: 'https://placer.example.org/trails-open-house-notice.pdf', duration: { minutes: 120, source: 'published', basis: 'The notice gives 4:00 to 6:00 PM.' } }),
  ];

  // Enough history to page through the earlier meetings ten at a time.
  const fillers = ['slt', 'truckee', 'ttd', 'ivgid'];
  const fillerTopics = [['transit'], ['housing'], ['environment'], []];
  for (let index = 0; index < 14; index += 1) {
    const agencyId = fillers[index % fillers.length];
    const id = `${agencyId}:${9000 + index}`;
    stored.push(withAgenda({ id, title: 'Regular Meeting', offset: -60 - 7 * index, time: '10:00' }, [
      { number: '1', title: `Budget Update ${index + 1}`, topics: fillerTopics[index % fillerTopics.length] },
    ]));
  }

  stored.sort((a, b) => a.date.localeCompare(b.date) || (a.startsAt ?? '').localeCompare(b.startsAt ?? '') || a.id.localeCompare(b.id));

  // Expected TTD meetings on its configured schedule (first Wednesday at 3:00 PM), skipping any within three days of a record.
  const ttdDays = stored.filter((record) => record.agencyId === 'ttd').map((record) => Date.parse(record.date));
  const forecasts = [];
  for (let month = 0; forecasts.length < 2; month += 1) {
    const first = new Date(Date.parse(`${today.slice(0, 7)}-01T12:00:00Z`));
    first.setUTCMonth(first.getUTCMonth() + month);
    while (first.getUTCDay() !== 3) first.setUTCDate(first.getUTCDate() + 1);
    const key = first.toISOString().slice(0, 10);
    if (key <= today || ttdDays.some((recorded) => Math.abs(recorded - Date.parse(key)) <= 3 * 86_400_000)) continue;
    // Forecasts carry no history (packages/domain/public.ts).
    const { history: _none, ...fields } = meeting({ id: `ttd:forecast-${key}`, title: 'TTD Board of Directors Meeting', offset: 0 });
    forecasts.push({
      ...fields, id: `forecast:ttd:${key}`, kind: 'forecast', date: key, startsAt: pacificInstant(key, '15:00'), officialUrl: exportedAgency('ttd').officialUrl,
      lastCheckedAt: null, updatedAt: generatedAt, change: null,
      forecast: { ruleLabel: 'TTD Board of Directors Meeting', basis: 'Usually the first Wednesday at 3:00 PM', source: 'configured' },
    });
  }

  const commentPeriods = [
    // Open, hosted under Other, linked to two meetings, moved from the Governing Board.
    {
      id: 'other:west-shore-trail-scoping', agencyId: 'other', host: 'TRPA and El Dorado County',
      title: 'West Shore Trail – Meeks Bay to D.L. Bliss State Park EA or EIS/EIR — scoping comment period',
      opensOn: day(-8), closesOn: day(12), closesAt: at(12, '17:00'), officialUrl: 'https://trpa.example.org/west-shore-trail-nop.pdf', participationUrl: 'https://trpa.example.org/comment',
      instructions: 'Submit written comments on the scope of the environmental document through the comment form on the TRPA website.',
      projectTitle: 'West Shore Trail – Meeks Bay to D.L. Bliss State Park', projectProponent: 'California Tahoe Conservancy',
      summary: 'A shared use path connecting Meeks Bay and D.L. Bliss State Park, first studied in the corridor plan at https://trpa.example.org/corridor-plan/.',
      meetingIds: [`other:one-off-west-shore-trail-scoping-${day(7)}`, `trpa-advisory:planned-${day(16)}`],
      previousIds: ['trpa-governing:west-shore-trail-scoping'], topics: ['biking', 'environment'],
      sourceQuote: 'Written comments on the scope and content of the EIS/EIR may be submitted during the scoping period.',
      sourceSha256: sha('west-shore-trail-nop'), lastCheckedAt: checked, updatedAt: checked,
    },
    // Closed, linked to a past meeting.
    {
      id: 'ttd:short-range-transit-plan', agencyId: 'ttd', title: 'Short Range Transit Plan public review draft',
      opensOn: day(-50), closesOn: day(-20), closesAt: null, officialUrl: 'https://ttd.example.org/short-range-transit-plan.pdf', participationUrl: null,
      instructions: null, projectTitle: null, projectProponent: null, summary: null, meetingIds: ['ttd:1001'], previousIds: [], topics: ['transit'],
      sourceQuote: 'Comments on the draft plan are due by the close of the review period.', sourceSha256: sha('ttd-srtp'), lastCheckedAt: checked, updatedAt: checked,
    },
    // Not yet open.
    {
      id: 'slt:housing-element-update', agencyId: 'slt', title: 'Housing Element Update draft',
      opensOn: day(5), closesOn: day(40), closesAt: null, officialUrl: 'https://slt.example.org/housing-element-draft.pdf', participationUrl: null,
      instructions: 'Email comments to the planning division.', projectTitle: 'Housing Element Update', projectProponent: null, summary: null,
      meetingIds: [], previousIds: [], topics: ['housing'],
      sourceQuote: 'The public review period for the draft Housing Element begins after the notice period.', sourceSha256: sha('slt-housing-element'), lastCheckedAt: checked, updatedAt: checked,
    },
  ];

  const agencyIds = new Set([...stored, ...commentPeriods].map((record) => record.agencyId));
  const firstUrl = (id) => stored.find((record) => record.agencyId === id)?.officialUrl;
  return {
    schemaVersion: 2, generatedAt, mode: 'live',
    coverageNote: 'Test fixture: made-up records for the website tests.',
    agencies: [...agencyIds].sort().map((id) => exportedAgency(id, firstUrl(id))),
    topics: catalog.map(({ id, name, description }) => ({ id, name, description })),
    meetings: [...stored, ...forecasts],
    commentPeriods,
  };
}
