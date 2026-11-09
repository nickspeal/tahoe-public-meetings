import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'astro/config';
import sitemap from './integrations/sitemap.mjs';

// The export the site is built from: written by `prebuild` (absent before the first fetch), or, for the
// test site, the fixture that TAHOE_PUBLIC_DATA names (scripts/build-test-site.mjs).
const dataFile = process.env.TAHOE_PUBLIC_DATA
  ? resolve(process.env.TAHOE_PUBLIC_DATA)
  : fileURLToPath(new URL('./src/data/public.json', import.meta.url));

/** `import data from 'virtual:tahoe-public-data'`: the export file, or undefined when there is none. */
function publicData() {
  const id = 'virtual:tahoe-public-data';
  const missing = '\0tahoe-public-data:missing';
  return {
    name: 'tahoe-public-data',
    resolveId: (source) => (source === id ? (existsSync(dataFile) ? dataFile : missing) : null),
    load: (resolved) => (resolved === missing ? 'export default undefined;' : null),
  };
}

const href = (id, agencyId) => {
  // Mirrors commentPeriodHref in src/lib/data.ts: stored IDs are `${agencyId}:${slug}`.
  const prefix = `${agencyId}:`;
  const slug = id.startsWith(prefix) ? id.slice(prefix.length) : id;
  return `/comment-periods/${encodeURIComponent(agencyId)}/${encodeURIComponent(slug)}/`;
};

/** A comment period that moved to another agency keeps its old page as a redirect (`previousIds`, config/README.md). */
function commentPeriodRedirects() {
  if (!existsSync(dataFile)) return {};
  const periods = JSON.parse(readFileSync(dataFile, 'utf8')).commentPeriods ?? [];
  const redirects = {};
  for (const period of periods) {
    for (const old of period.previousIds ?? []) {
      const [oldAgency] = old.split(':');
      if (old.includes(':')) redirects[href(old, oldAgency)] = href(period.id, period.agencyId);
    }
  }
  return redirects;
}

export default defineConfig({
  // Canonical production origin (www redirects here at the Vercel domain level).
  site: 'https://tahoepublicmeetings.org',
  output: 'static',
  // The test site builds to its own folder so it never replaces the site built from the real export.
  outDir: process.env.TAHOE_OUT_DIR || './dist',
  redirects: commentPeriodRedirects(),
  // Writes dist/sitemap.xml after the build (integrations/sitemap.mjs).
  integrations: [sitemap()],
  vite: { plugins: [publicData()] },
});
