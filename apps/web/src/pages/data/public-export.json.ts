import type { APIRoute } from 'astro';
import exported from 'virtual:tahoe-public-data';

/** The export this build used, unchanged. A later build reads it when the database is unavailable (scripts/fetch-export.mjs). */
export const GET: APIRoute = () =>
  new Response(JSON.stringify(exported) + '\n', {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'public, max-age=0, must-revalidate' },
  });
