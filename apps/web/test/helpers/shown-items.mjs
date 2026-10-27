import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/routine-items.ts'), 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
export const { isRoutineItem, isRoutineTitle } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);

/** Items a meeting page lists: not suppressed by the operator and not routine meeting mechanics. */
export const shown = (item) => !item.suppressed && !isRoutineItem(item);
