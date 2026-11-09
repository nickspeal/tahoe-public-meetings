/** The export the site is built from; see the publicData plugin in astro.config.mjs. */
declare module 'virtual:tahoe-public-data' {
  import type { PublicData } from '../../../packages/domain/public';
  const data: PublicData | undefined;
  export default data;
}
