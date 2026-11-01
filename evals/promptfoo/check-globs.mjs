// Compatibility canary for the exact public APIs used by Promptfoo and its
// optional filesystem provider. The provider itself is never started here.
import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, realpathSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename, dirname, join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {escape, glob, globSync, Glob, hasMagic} from 'glob';
import {globSync as rawGlobSync} from 'glob/raw';
import {minimatch} from 'minimatch';
import {Client, parseList} from 'basic-ftp';
import {getUri} from 'get-uri';

const expected = ['prompts/nested/prompt-b.txt', 'prompts/prompt-a.txt'];
const files = [...expected, 'prompts/.draft.txt', 'data/input.json',
  'data/private/secret.json', 'data/nested/Test.JSON', 'data/drop.tmp', 'literal[1].txt'];
const parent = realpathSync(tmpdir());
const root = realpathSync(mkdtempSync(join(parent, 'surface-map-globs-')));
const options = {cwd: root, nodir: true, posix: true};
try {
  for (const file of files) {
    const path = join(root, file);
    mkdirSync(dirname(path), {recursive: true});
    writeFileSync(path, 'Inert glob compatibility fixture.\n', {flag: 'wx'});
  }
  assert.equal(hasMagic('prompts/**/*.txt'), true);
  assert.equal(hasMagic('prompts/prompt-a.txt'), false);
  assert.deepEqual(globSync('prompts/**/*.txt', options).sort(), expected);
  assert.deepEqual(rawGlobSync('prompts/**/*.txt', options).sort(), expected);
  assert.deepEqual((await glob(['prompts/*.txt', 'prompts/nested/*.txt'], options)).sort(), expected);
  assert.deepEqual(new Glob('prompts/**/*.txt', options).walkSync().sort(), expected);
  assert.deepEqual([...new Glob('prompts/**/*.txt', options).iterateSync()].sort(), expected);
  assert.deepEqual(globSync('prompts\\**\\*.txt', {...options, windowsPathsNoEscape: true}).sort(), expected);
  assert.deepEqual(globSync('prompts/**/*.txt', {...options, dot: true}).sort(),
    ['prompts/.draft.txt', ...expected]);
  assert.deepEqual(globSync('data/**/*.json', {...options, nocase: true, ignore: ['**/private/**']}).sort(),
    ['data/input.json', 'data/nested/Test.JSON']);
  assert.deepEqual(globSync('{prompts/**/*.txt,data/*.json}', options).sort(), ['data/input.json', ...expected]);
  assert.deepEqual(globSync(escape('literal[1].txt'), options), ['literal[1].txt']);
  assert.equal(hasMagic(escape('literal[1].txt')), false);

  // These calls mirror public minimatch() matching and exclusion patterns in
  // Promptfoo's scanner and the filesystem provider, without starting a server.
  for (const [path, pattern, opts, wanted] of [
    ['prompts/prompt-a.txt', '**/*.txt', {}, true],
    ['data/input.json', '**/*.txt', {}, false],
    ['prompts/.draft.txt', '**/*.txt', {}, false],
    ['prompts/.draft.txt', '**/*.txt', {dot: true}, true],
    ['data/private/secret.json', '**/private/**', {dot: true}, true],
    ['data/input.json', '**/private/**', {dot: true}, false],
    ['data/nested/Test.JSON', '**/*.json', {nocase: true}, true],
  ]) assert.equal(minimatch(path, pattern, opts), wanted, `${path} vs ${pattern}`);

  // get-uri imports Client and uses these methods. Validate that entrypoint and
  // a small directory-listing specimen without opening any FTP connection.
  assert.equal(typeof getUri, 'function');
  for (const method of ['access', 'lastMod', 'list', 'downloadTo', 'close']) {
    assert.equal(typeof Client.prototype[method], 'function', `FTP Client.${method}`);
  }
  const listing = '-rw-r--r-- 1 owner group 42 Jan 1 2020 fixture.txt\r\n';
  const parsed = parseList('-rw-r--r-- 1 ' + 'a '.repeat(64) + '!\r\n' + listing);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].name, 'fixture.txt');
  assert.equal(parsed[0].size, 42);
  // Unknown MLSD fact names must be ignored rather than resolved through an
  // inherited object property. This one-line regression uses no connection.
  const mlsd = parseList('type=file;__proto__=ignored;size=42; mlsd-fixture.txt\r\n');
  assert.equal(mlsd.length, 1);
  assert.equal(mlsd[0].name, 'mlsd-fixture.txt');
  assert.equal(mlsd[0].size, 42);

  // A short amplification-pattern regression runs in a separate process with
  // a hard deadline. It checks the documented preprocessing cap, not timing on
  // a large denial-of-service payload. Eight sections keep the probe small even
  // if an old vulnerable matcher is accidentally reintroduced.
  const probe = spawnSync(process.execPath, ['--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import {Minimatch} from 'minimatch';
    import {Glob} from 'glob';
    const pattern = Array.from({length: 8}, (_, i) => '**/../a' + i + '/b' + i).join('/') + '/target.txt';
    const matcher = new Minimatch(pattern, {optimizationLevel: 2, braceExpandMax: 16});
    assert.ok(matcher.set.length <= 16);
    assert.ok(matcher.globParts.length <= 16);
    const bundledGlob = new Glob(pattern, {cwd: process.cwd(), braceExpandMax: 16});
    assert.ok(bundledGlob.patterns.length <= 16);
    const part = new Glob('*.txt', {cwd: process.cwd()}).patterns[0].pattern();
    assert.equal(part instanceof RegExp, false);
    assert.equal(typeof part.test, 'function');
  `], {cwd: fileURLToPath(new URL('.', import.meta.url)), timeout: 3000, encoding: 'utf8'});
  assert.equal(probe.error, undefined, 'Preprocessing probe exceeded its deadline or could not start');
  assert.equal(probe.status, 0, 'Preprocessing expansion must respect braceExpandMax');
  console.log('Dependency compatibility passed: glob/matcher APIs, real file selection, bounded preprocessing, get-uri entrypoint and offline FTP listing parser.');
} finally {
  // Restrict recursive cleanup to the exact temporary fixture directory.
  assert.equal(dirname(root), parent);
  assert.ok(basename(root).startsWith('surface-map-globs-'));
  rmSync(root, {recursive: true, force: true});
}
