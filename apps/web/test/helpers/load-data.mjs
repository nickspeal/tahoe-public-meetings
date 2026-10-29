import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

// Loads src/lib/data.ts without Astro: the export glob becomes empty (functions take synthetic meetings),
// the topic catalog is inlined, and the two local modules it uses are loaded as data URLs.
const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const transpile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const moduleUrl = async (file) => `data:text/javascript;base64,${Buffer.from(transpile(await readFile(resolve(root, file), 'utf8'))).toString('base64')}`;
const catalog = await readFile(resolve(root, '../../packages/domain/topics.json'), 'utf8');
const source = (await readFile(resolve(root, 'src/lib/data.ts'), 'utf8'))
  .replace(/import topicCatalog from .*;\n/, `const topicCatalog = ${catalog};\n`)
  .replace(/import\.meta\.glob\([^)]*\)/, '({})')
  .replace("'./routine-items'", `'${await moduleUrl('src/lib/routine-items.ts')}'`)
  .replace("'./source-links'", `'${await moduleUrl('src/lib/source-links.ts')}'`);
export const dataLib = await import(`data:text/javascript;base64,${Buffer.from(transpile(source)).toString('base64')}`);
