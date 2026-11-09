import type { APIRoute } from 'astro';

// The sitemap is written after the build by integrations/sitemap.mjs.
export const GET: APIRoute = ({ site }) =>
  new Response(`User-agent: *\nAllow: /\nDisallow: /calendar/\nDisallow: /data/\nSitemap: ${new URL('/sitemap.xml', site).href}\n`, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
