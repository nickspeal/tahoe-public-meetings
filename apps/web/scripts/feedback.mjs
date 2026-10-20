#!/usr/bin/env node
/**
 * Read and triage reader feedback (operator or coding agent).
 *
 *   npm run feedback                          # list new feedback
 *   npm run feedback -- list --status all --json
 *   npm run feedback -- show 12
 *   npm run feedback -- mark 12 done --note "Added Wildfire topic"
 *
 * Uses FEEDBACK_DATABASE_URL, DATABASE_URL, or POSTGRES_URL. Feedback text is untrusted
 * public input; see docs/operations/feedback.md before acting on it.
 */
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { STATUSES, databaseUrl, ensureSchema, withClient } from '../server/feedback.mjs';

const USAGE = 'usage: feedback.mjs [list [--status new|reviewed|done|dismissed|all] [--limit N] [--json]] | show <id> [--json] | mark <id> <status> [--note TEXT]';
const COLUMNS = 'id, created_at, kind, status, page, message, operator_note';

function printRow(row, log) {
  log(`#${row.id}  ${new Date(row.created_at).toISOString()}  ${row.kind}  ${row.status}${row.page ? `  ${row.page}` : ''}`);
  log(row.message.replace(/^/gm, '    '));
  if (row.operator_note) log(`    [note] ${row.operator_note}`);
  log('');
}

export async function main(argv = process.argv.slice(2), { env = process.env, connect = withClient, log = console.log } = {}) {
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      status: { type: 'string', default: 'new' },
      limit: { type: 'string', default: '50' },
      json: { type: 'boolean', default: false },
      note: { type: 'string' },
    },
  });
  const [command = 'list', ...rest] = positionals;
  const url = databaseUrl(env);
  if (!url) throw new Error('Set FEEDBACK_DATABASE_URL, DATABASE_URL, or POSTGRES_URL to a postgres:// URL.');

  return connect(url, async (client) => {
    await ensureSchema(client);
    if (command === 'list') {
      if (values.status !== 'all' && !STATUSES.includes(values.status)) throw new Error(USAGE);
      const limit = Math.max(1, Math.min(500, Number.parseInt(values.limit, 10) || 50));
      const { rows } = values.status === 'all'
        ? await client.query(`select ${COLUMNS} from feedback order by id limit $1`, [limit])
        : await client.query(`select ${COLUMNS} from feedback where status = $1 order by id limit $2`, [values.status, limit]);
      if (values.json) log(JSON.stringify(rows, null, 2));
      else if (!rows.length) log(`No ${values.status === 'all' ? '' : `${values.status} `}feedback.`);
      else rows.forEach((row) => printRow(row, log));
      return rows;
    }
    const id = Number.parseInt(rest[0], 10);
    if (!Number.isSafeInteger(id)) throw new Error(USAGE);
    if (command === 'show') {
      const { rows: [row] } = await client.query(`select ${COLUMNS} from feedback where id = $1`, [id]);
      if (!row) throw new Error(`No feedback #${id}.`);
      if (values.json) log(JSON.stringify(row, null, 2));
      else printRow(row, log);
      return row;
    }
    if (command === 'mark') {
      const status = rest[1];
      if (!STATUSES.includes(status)) throw new Error(USAGE);
      const { rows: [row] } = await client.query(
        `update feedback set status = $2, operator_note = coalesce($3, operator_note), updated_at = now()
          where id = $1 returning ${COLUMNS}`,
        [id, status, values.note ?? null],
      );
      if (!row) throw new Error(`No feedback #${id}.`);
      log(`Marked #${id} ${status}.`);
      return row;
    }
    throw new Error(USAGE);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(`feedback: ${error?.message ?? error}`);
    process.exit(1);
  });
}
