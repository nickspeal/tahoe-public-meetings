import type { APIRoute } from 'astro';

const publicDataModules = import.meta.glob('../../data/public.json', { eager: true, import: 'default' });
const exported = publicDataModules['../../data/public.json'];

/** The export this build used, unchanged. A later build reads it when the database is unavailable (scripts/fetch-export.mjs). */
export const GET: APIRoute = () =>
  new Response(JSON.stringify(exported) + '\n', {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'public, max-age=0, must-revalidate' },
  });
