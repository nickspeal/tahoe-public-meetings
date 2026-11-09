import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// The test site: the fixture export (test/fixtures/public-data.mjs) and the site built from it, both written by
// scripts/build-test-site.mjs before the tests run (`npm test`). Never the real export in src/data/public.json.
const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
export const site = resolve(root, '.test-site');
export const dist = resolve(site, 'dist');
export const data = JSON.parse(readFileSync(resolve(site, 'public.json'), 'utf8'));

/** A fixture record by ID; a missing one means the fixture and the test disagree. */
export function record(id) {
  const found = data.meetings.find((meeting) => meeting.id === id);
  if (!found) throw new Error(`the fixture has no meeting ${id}`);
  return found;
}

/** The fixture's records whose IDs start with `prefix`, e.g. the planned meeting `slt:planned-` dated from today. */
export function recordStartingWith(prefix) {
  const found = data.meetings.find((meeting) => meeting.id.startsWith(prefix));
  if (!found) throw new Error(`the fixture has no meeting ${prefix}…`);
  return found;
}
