import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

// Loads src/lib/meeting-paths.ts and resolves the built site's page path for each stored meeting in the export.
const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/meeting-paths.ts'), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
export const paths = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
export const agencyCatalog = JSON.parse(await readFile(resolve(root, '../../packages/domain/agencies.json'), 'utf8'));

let exportPaths;
/** "/meetings/south-lake-tahoe-city-council/2026-11-03/" for a stored meeting in src/data/public.json. */
export async function meetingPath(meeting) {
  if (!exportPaths) {
    const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
    exportPaths = paths.meetingPaths(data.meetings.filter((row) => row.kind !== 'forecast'), paths.agencyPages(agencyCatalog));
  }
  return exportPaths.get(meeting.id);
}

/** The built index.html for a stored meeting's page. */
export const meetingFile = async (meeting) => resolve(root, 'dist', ...(await meetingPath(meeting)).split('/').filter(Boolean), 'index.html');
