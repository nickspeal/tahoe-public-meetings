#!/usr/bin/env node
/**
 * Builds the site the web tests read: the fixture export (test/fixtures/public-data.mjs), dated from today,
 * written to .test-site/public.json and checked by scripts/check_export.py, then built into .test-site/dist.
 * It never reads or replaces src/data/public.json or dist/.
 */
import { spawnSync } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildFixture } from '../test/fixtures/public-data.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const site = resolve(root, '.test-site');
const dataFile = resolve(site, 'public.json');
const outDir = resolve(site, 'dist');

function run(command, args, env = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', env: { ...process.env, ...env } });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

await rm(site, { recursive: true, force: true });
await mkdir(site, { recursive: true });
await writeFile(dataFile, `${JSON.stringify(buildFixture(), null, 1)}\n`);
run('python3', [resolve(root, '../../scripts/check_export.py'), dataFile]);
run(resolve(root, 'node_modules/.bin/astro'), ['build', '--silent'], { TAHOE_PUBLIC_DATA: dataFile, TAHOE_OUT_DIR: outDir, ASTRO_TELEMETRY_DISABLED: '1' });
