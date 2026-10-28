import type { AgendaItem, Meeting, PublicDocument } from '../../../../packages/domain/public';

/** Add a PDF open parameter, keeping any fragment the official URL already has. */
export function citationHref(url: string, page: number, mediaType = "application/pdf") {
  if (mediaType !== "application/pdf") return url;
  const splitAt = url.indexOf('#');
  if (splitAt < 0) return `${url}#page=${page}`;
  const base = url.slice(0, splitAt);
  const fragment = url.slice(splitAt + 1);
  const delimiter = fragment ? '&' : '';
  return `${base}#${fragment}${delimiter}page=${page}`;
}

const HTML_PATH = /\.(?:html?|aspx?|php)$/i;

/** Browser PDF viewers honor #page=N; an HTML page has no page to jump to. */
export function isPdfDocument(document: Pick<PublicDocument, 'url' | 'pageCount' | 'mediaType'>) {
  if (document.mediaType) return document.mediaType === "application/pdf";
  let url: URL;
  try {
    url = new URL(document.url);
  } catch {
    return false;
  }
  if (HTML_PATH.test(url.pathname)) return false;
  if (/\.pdf$/i.test(url.pathname) || /\.pdf(?:$|&)/i.test(url.search)) return true;
  // Stored page counts come from PDF text extraction, e.g. document-center links without an extension.
  return document.pageCount > 0;
}

/** Direct link to the official document at an agenda item's cited page, shown as a card. */
export function itemSourceLink(meeting: Pick<Meeting, 'documents'>, item: Pick<AgendaItem, 'citations'>) {
  const citation = item.citations[0];
  const document = citation && meeting.documents.find((candidate) => candidate.id === citation.documentId);
  if (!citation || !document) return null;
  const label = /packet/i.test(document.title) ? 'Read in the agenda packet' : 'Read in the agenda';
  return isPdfDocument(document)
    ? { href: citationHref(document.url, citation.page), label, detail: `Page ${citation.page} · official PDF` }
    : { href: document.url, label, detail: 'Official agency webpage' };
}

const WORD_TYPES = new Set([
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
]);

/** Direct link to the item's own staff report (IDEA-013), shown as a second card beside the agenda link. */
export function staffReportLink(item: Pick<AgendaItem, 'staffReport'>) {
  const report = item.staffReport;
  if (!report) return null;
  const label = report.title === 'Legislation text' ? 'Read the legislation text'
    : report.kind === 'executive-summary' ? 'Read the executive summary'
    : report.kind === 'staff-memo' ? 'Read the staff memo'
    : 'Read the staff report';
  const mediaType = report.mediaType ?? 'application/pdf';
  if (mediaType !== 'application/pdf') {
    const detail = WORD_TYPES.has(mediaType) ? 'Official Word document' : 'Official agency webpage';
    return { href: report.url, label, detail };
  }
  const inPacket = report.page > 1 || report.pageEnd < report.pageCount;
  const detail = inPacket
    ? `${report.pageEnd > report.page ? `Pages ${report.page}–${report.pageEnd}` : `Page ${report.page}`} of the agenda packet · official PDF`
    : 'Official PDF';
  return { href: citationHref(report.url, report.page), label, detail };
}
