import assert from 'node:assert/strict';
import test from 'node:test';
import { dataLib } from './helpers/load-data.mjs';

const agency = (status, lastCheckedAt = '2026-10-20T15:24:17Z') => ({ id: 'placer-supervisors', name: 'Placer', shortName: 'Placer',
  officialUrl: 'https://www.placer.ca.gov/bos', sourceUrl: 'https://www.placer.ca.gov/10616/_2026', lastCheckedAt, status, error: null });

test('a blocked source says so instead of showing its last good check', () => {
  assert.equal(dataLib.agencySourceLabel(agency('blocked')), 'Source blocked; listed records may be out of date');
  assert.equal(dataLib.agencySourceLabel(agency('error')), 'Could not verify latest source');
  assert.match(dataLib.agencySourceLabel(agency('ok')), /2026/);
});
