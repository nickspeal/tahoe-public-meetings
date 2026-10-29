import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/meeting-title.ts'), 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { meetingCardTitle } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);

const agency = (shortName, name = shortName) => ({ shortName, name });

test('titles that already name the agency are kept', () => {
  assert.equal(meetingCardTitle('El Dorado County Board of Supervisors Meeting', agency('El Dorado County', 'El Dorado County — Board of Supervisors')),
    'El Dorado County Board of Supervisors Meeting');
  assert.equal(meetingCardTitle('Placer County Transportation Planning Agency Board of Directors Meeting', agency('Placer County', 'Placer County — Transportation Planning Agency')),
    'Placer County Transportation Planning Agency Board of Directors Meeting');
  assert.equal(meetingCardTitle('Nevada Division of State Parks Public Hearing on Regulations', agency('Nevada State Parks')),
    'Nevada Division of State Parks Public Hearing on Regulations');
  assert.equal(meetingCardTitle('Tahoe City Public Utility District Board of Directors Meeting', agency('TCPUD', 'TCPUD — Board')),
    'Tahoe City Public Utility District Board of Directors Meeting');
});

test('titles that do not name the agency get its short name in front', () => {
  assert.equal(meetingCardTitle('Town Council Meeting', agency('Truckee', 'Town of Truckee')), 'Truckee Town Council Meeting');
  assert.equal(meetingCardTitle('Special Board of Directors Meeting/Public Workshop', agency('TCPUD', 'TCPUD — Board')),
    'TCPUD Special Board of Directors Meeting/Public Workshop');
  assert.equal(meetingCardTitle('Marine Resources Committee', agency('California Fish and Game', 'California Fish and Game — Commission and Committees')),
    'California Fish and Game Marine Resources Committee');
});

test('dates and filing words are dropped', () => {
  assert.equal(meetingCardTitle('Governing Board Documents October 28, 2026-Hybrid Meeting', agency('TRPA', 'TRPA — Governing Board')),
    'TRPA Governing Board Meeting');
  assert.equal(meetingCardTitle('TTD Board of Directors Meeting – November 4, 2026', agency('TTD', 'Tahoe Transportation District')),
    'TTD Board of Directors Meeting');
  assert.equal(meetingCardTitle('City Council Meeting Agenda — August 11, 2026', agency('South Lake Tahoe', 'City of South Lake Tahoe')),
    'South Lake Tahoe City Council Meeting');
  assert.equal(meetingCardTitle('California Fish and Game Commission Meeting (June 17–18, 2026)', agency('California Fish and Game')),
    'California Fish and Game Commission Meeting');
});

test('a canceled meeting drops the cancellation words its badge already shows', () => {
  const washoe = agency('Washoe County', 'Washoe County — Board of Commissioners');
  assert.equal(meetingCardTitle('Washoe County Board of County Commissioners Regular Meeting (2026-09-22; canceled)', washoe, { canceled: true }),
    'Washoe County Board of County Commissioners Regular Meeting');
  assert.equal(meetingCardTitle('Lahontan Regional Water Quality Control Board Meeting — canceled (June 10–11, 2026)', agency('Lahontan', 'Lahontan — Board')),
    'Lahontan Regional Water Quality Control Board Meeting — canceled');
});

test('a named host takes the agency\'s place in front of a title', () => {
  const host = 'TRPA and El Dorado County';
  assert.equal(meetingCardTitle('Scoping Open House', agency(host)), 'TRPA and El Dorado County Scoping Open House');
  assert.equal(meetingCardTitle('TRPA and El Dorado County Scoping Open House', agency(host)), 'TRPA and El Dorado County Scoping Open House');
});

test('space- and dot-separated numeric dates are dropped', () => {
  assert.equal(meetingCardTitle('09 22 2026 City Council', agency('South Lake Tahoe', 'South Lake Tahoe — City Council')), 'South Lake Tahoe City Council');
  assert.equal(meetingCardTitle('10 29 2026 South Tahoe Transit Joint Powers Authority', agency('Tahoe Transportation District')),
    'Tahoe Transportation District South Tahoe Transit Joint Powers Authority');
  assert.equal(meetingCardTitle('Board Meeting 09.28.2026', agency('TCPUD')), 'TCPUD Board Meeting');
});
