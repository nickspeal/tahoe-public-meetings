import { defineConfig } from 'astro/config';

export default defineConfig({
  // Canonical production origin (www redirects here at the Vercel domain level).
  site: 'https://tahoepublicmeetings.org',
  output: 'static',
});
