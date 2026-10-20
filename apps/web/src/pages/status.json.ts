import type { APIRoute } from 'astro';
import { data, meetings } from '../lib/data';

/** Machine-readable freshness for the external watchdog (.github/workflows/freshness.yml). */
export const GET: APIRoute = () =>
  new Response(JSON.stringify({ generatedAt: data.generatedAt, mode: data.mode, meetings: meetings.length }) + '\n', {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'public, max-age=0, must-revalidate' },
  });
