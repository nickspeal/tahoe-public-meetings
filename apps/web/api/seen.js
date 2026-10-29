// Vercel function: POST /api/seen counts a page view on the baseline GoatCounter site (server/pageview-baseline.mjs).
import { createBaselineHandler } from '../server/pageview-baseline.mjs';

const handle = createBaselineHandler();

export function POST(request) {
  return handle(request);
}
