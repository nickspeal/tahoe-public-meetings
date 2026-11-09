import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

// Paths robots.txt disallows: calendar files and raw data, not pages for readers.
const DISALLOWED = ['/calendar/', '/data/'];

async function htmlFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true, recursive: true });
  return entries.filter((entry) => entry.isFile() && entry.name === 'index.html').map((entry) => join(entry.parentPath, entry.name));
}

const escapeXml = (value) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/**
 * Writes dist/sitemap.xml from the built pages, so it follows whatever routes exist without restating them.
 * A page is listed only when it is its own canonical URL: SiteLayout omits the canonical link on noindex pages,
 * and Astro's redirect pages (comment periods that moved) point theirs at the target. No lastmod: most pages
 * change with every export, and a date that is not true is worse than none.
 */
export default function sitemap() {
  let site;
  return {
    name: 'tahoe-sitemap',
    hooks: {
      'astro:config:done': ({ config }) => { site = config.site; },
      'astro:build:done': async ({ dir, logger }) => {
        if (!site) throw new Error('sitemap: astro.config.mjs must set `site`');
        const root = fileURLToPath(dir);
        const urls = [];
        for (const file of await htmlFiles(root)) {
          const folder = relative(root, join(file, '..')).split(sep).filter(Boolean).join('/');
          const path = folder ? `/${folder}/` : '/';
          if (DISALLOWED.some((prefix) => path.startsWith(prefix))) continue;
          const html = await readFile(file, 'utf8');
          if (/<meta[^>]+name="robots"[^>]+noindex/i.test(html) || /<meta[^>]+http-equiv="refresh"/i.test(html)) continue;
          const loc = new URL(path, site).href;
          const canonical = html.match(/<link[^>]+rel="canonical"[^>]+href="([^"]+)"/i)?.[1];
          if (canonical !== loc) continue;
          urls.push(loc);
        }
        urls.sort();
        const body = urls.map((loc) => `  <url><loc>${escapeXml(loc)}</loc></url>`).join('\n');
        await writeFile(join(root, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`);
        logger.info(`sitemap.xml: ${urls.length} pages`);
      },
    },
  };
}
