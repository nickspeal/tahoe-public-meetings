// The part of GoatCounter's count.js (lib/analytics.ts) that page scripts call. SiteLayout sets
// `window.goatcounter = { path }` before count.js loads, so `count` may be missing: call it as
// `window.goatcounter?.count?.(...)`. Without analytics configured, `window.goatcounter` is undefined.
interface Window {
  goatcounter?: {
    path?: (path: string) => string | null;
    count?: (vars: { path: string; event?: boolean; referrer?: string; title?: string }) => void;
  };
}
