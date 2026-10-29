import { existsSync, readFileSync } from 'node:fs';
import { defineConfig } from 'astro/config';

const href = (id, agencyId) => {
  // Mirrors commentPeriodHref in src/lib/data.ts: stored IDs are `${agencyId}:${slug}`.
  const prefix = `${agencyId}:`;
  const slug = id.startsWith(prefix) ? id.slice(prefix.length) : id;
  return `/comment-periods/${encodeURIComponent(agencyId)}/${encodeURIComponent(slug)}/`;
};

/** A comment period that moved to another agency keeps its old page as a redirect (`previousIds`, config/README.md). */
function commentPeriodRedirects() {
  const path = new URL('./src/data/public.json', import.meta.url); // Written by `prebuild`; absent before the first fetch.
  if (!existsSync(path)) return {};
  const periods = JSON.parse(readFileSync(path, 'utf8')).commentPeriods ?? [];
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
  redirects: commentPeriodRedirects(),
});
