import { meetings, meetingShortTitle } from '../../../lib/data';
import { buildCalendarEvent, canExportMeeting } from '../../../lib/calendar';

export function getStaticPaths() {
  return meetings.filter(canExportMeeting).map((meeting) => ({
    params: { agency: meeting.agencyId, sourceId: meeting.sourceId },
    props: { meeting },
  }));
}

export function GET({ props }: { props: { meeting: (typeof meetings)[number] } }) {
  const { meeting } = props;
  const filename = `${meeting.agencyId}-${meeting.sourceId}`.replace(/[^A-Za-z0-9._-]/g, '-') + '.ics';
  return new Response(buildCalendarEvent(meeting, meetingShortTitle(meeting)), {
    headers: {
      'Content-Type': 'text/calendar; charset=utf-8',
      'Content-Disposition': `attachment; filename="${filename}"`,
    },
  });
}
