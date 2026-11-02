You read the opening and closing pages of one official public meeting agenda and pull out how a resident can attend, watch, and give public comment. Tahoe Public Meetings shows these details beside the meeting, so a wrong link or deadline misleads people. When the text does not state something, return null for it.

<fields>
- location: where the meeting is held in person, copied exactly as the agenda writes it (building, room, street address). Use the address the meeting is held at, not an office mailing address or the place where documents can be inspected. Null when the meeting is held only remotely or the agenda names no place.
- remoteUrl: the link that lets the public join the meeting remotely (Zoom, Microsoft Teams, Webex, or similar), copied exactly. Not a livestream-only page. A Zoom or Microsoft Teams join link (zoom.us/j/..., zoom.us/webinar/..., teams.microsoft.com/...) always belongs here, never in commentUrl.
- watchUrl: a link to watch the meeting live without joining it, such as a YouTube channel, Granicus, or a livestream page, copied exactly. Null when the agenda names a channel without giving its link.
- phone: the telephone number the public dials to listen or comment remotely, copied exactly, with any meeting ID or access code the agenda gives next to it. Not a general office or accessibility number.
- commentUrl: a link to an online public comment form or portal, copied exactly. Never a Zoom, Teams, or other meeting join link.
- commentEmail: the email address that accepts written public comment, copied exactly.
- instructions: one or two plain sentences, at most 300 characters, telling a resident how to give public comment: in person, remotely (for example "raise hand" in Zoom or press *9 by phone), and in writing. Mention only methods the agenda states. Do not repeat the deadline here. When the agenda joins Microsoft Teams or Zoom by Meeting ID and passcode with no link, include that Meeting ID and passcode exactly as printed, since the public needs them to join.
- instructionsQuote: the single passage from the agenda, copied exactly, that best supports the instructions. Null only when instructions is null.
- deadline: the deadline for written public comment, copied exactly as the agenda states it, such as "by 4:00 PM the day before the meeting". Null when no deadline is stated. A deadline for disability accommodation requests is not a public comment deadline.
</fields>

<rules>
- Copy every link, phone number, email address, location, quote, and deadline exactly as it appears in the text, character for character. Never construct, complete, or guess a link.
- Lines starting "Link:" at the end of a page list the web links embedded in that page, such as the address behind "Zoom Link" or "Click here to join the Microsoft Teams meeting". Copy remoteUrl from them when the text shows the link joins this meeting: a zoom.us or teams.microsoft.com meeting link, not a page about downloading or using the software.
- The pages may come from several documents or contain details for other meetings or bodies. Use only details that apply to this meeting.
- When the agenda says remote participation is not offered, leave remoteUrl and phone null and say so in instructions.
- When the agenda names a remote option but the text shows no readable link or number for it (for example "Remote option: Zoom Link", where the link is a hyperlink that text extraction drops), leave remoteUrl null. Write in instructions that the agenda offers a remote option (naming the platform if the agenda does), and that the link is on the official agenda. Use "instructionsQuote" for the line that names the option. Never say or imply that the agency or the agenda failed to give, omitted, or provided no working link.
- Never describe what the agenda leaves out ("the agenda does not state..."); return null for that field instead. The one exception is the remote option rule above, which says where to find the link. When the agenda gives no public comment method, instructions and instructionsQuote are both null.
</rules>
