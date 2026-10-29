// Checks available without Python. Does not validate the Python application.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const db = new DatabaseSync(':memory:');
try {
  db.exec('BEGIN IMMEDIATE');
  db.exec(fs.readFileSync(path.join(root, 'apps/server/life_os/migrations/001_initial.sql'), 'utf8'));
  db.exec(fs.readFileSync(path.join(root, 'apps/server/life_os/migrations/002_devices.sql'), 'utf8'));
  db.exec('COMMIT');
  assert.equal(db.prepare('PRAGMA user_version').get().user_version, 2);
  for (const table of ['metadata', 'records', 'operations', 'changes', 'devices', 'pairing']) {
    assert.ok(db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name=?").get(table));
  }
  const insert = db.prepare('INSERT INTO records VALUES (?, ?, ?, ?, ?)');
  assert.throws(() => insert.run('invalid', 'id', 1, 0, '{}'));
  assert.throws(() => insert.run('routine', 'id', 0, 0, '{}'));
  db.exec('BEGIN IMMEDIATE');
  insert.run('routine', 'id', 1, 0, '{}');
  db.prepare('INSERT INTO changes(record) VALUES (?)').run('{}');
  db.exec('ROLLBACK');
  assert.equal(db.prepare('SELECT COUNT(*) AS total FROM records').get().total, 0);
  assert.equal(db.prepare('SELECT COUNT(*) AS total FROM changes').get().total, 0);
} finally {
  db.close();
}

const schema = JSON.parse(fs.readFileSync(path.join(root, 'contracts/sync-v1.schema.json'), 'utf8'));
let refs = 0;
function inspect(value) {
  if (!value || typeof value !== 'object') return;
  if (value.$ref) {
    assert.ok(value.$ref.startsWith('#/'));
    const resolved = value.$ref.slice(2).split('/').reduce(
      (node, key) => node?.[key.replace(/~1/g, '/').replace(/~0/g, '~')], schema,
    );
    assert.ok(resolved, `Unresolved reference: ${value.$ref}`);
    refs++;
  }
  Object.values(value).forEach(inspect);
}
inspect(schema);
let links = 0;
let markdownFiles = 0;
function inspectDocs(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (['.cache', '.codex', '.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.data'].includes(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) inspectDocs(file);
    else if (entry.name.endsWith('.md')) {
      markdownFiles++;
      const source = fs.readFileSync(file, 'utf8');
      assert.ok(!source.includes('\uFFFD'), `Encoding: ${file}`);
      for (const match of source.matchAll(/\]\(([^)]+)\)/g)) {
        if (/^(https?:|#)/.test(match[1])) continue;
        assert.ok(fs.existsSync(path.resolve(path.dirname(file), match[1].split('#')[0])), `${file}: ${match[1]}`);
        links++;
      }
    }
  }
}
inspectDocs(root);
console.log('OK: SQLite migration, constraints and transaction rollback.');
console.log(`OK: ${refs} schema references; ${links} local links in ${markdownFiles} Markdown files.`);
console.log('Not checked: full JSON Schema validation, Python tests, HTTP runtime or clients.');
