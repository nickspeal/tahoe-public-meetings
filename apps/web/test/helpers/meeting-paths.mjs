import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { data, dist } from './site.mjs';

// Loads src/lib/meeting-paths.ts and resolves the test site's page path for each stored meeting in the fixture export.
const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/meeting-paths.ts'), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
export const paths = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
export const agencyCatalog = JSON.parse(await readFile(resolve(root, '../../packages/domain/agencies.json'), 'utf8'));

const exportPaths = paths.meetingPaths(data.meetings.filter((row) => row.kind !== 'forecast'), paths.agencyPages(agencyCatalog));
/** "/meetings/south-lake-tahoe-city-council/2026-11-03/" for a stored meeting in the fixture export. */
export async function meetingPath(meeting) {
  return exportPaths.get(meeting.id);
}

/** The built index.html for a stored meeting's page. */
export const meetingFile = async (meeting) => resolve(dist, ...(await meetingPath(meeting)).split('/').filter(Boolean), 'index.html');
