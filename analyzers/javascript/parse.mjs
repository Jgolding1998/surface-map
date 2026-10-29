import {registerHooks} from 'node:module';

const parserURL = new URL('./node_modules/@babel/parser/lib/index.js', import.meta.url).href;
const deny = () => {throw new Error('Import denied');};
registerHooks({
  resolve(specifier, context, next) {if (specifier !== parserURL) return deny(); return next(specifier, context);},
  load(url, context, next) {if (url !== parserURL) return deny(); return next(url, context);},
});
for (const name of ['fetch', 'WebSocket']) Object.defineProperty(globalThis, name, {value: undefined, writable: false, configurable: false});
Object.defineProperty(process, 'getBuiltinModule', {value: undefined, writable: false, configurable: false});
const {parse} = await import(parserURL);

const MAX_SOURCE = 512 * 1024, MAX_INPUT = MAX_SOURCE * 6 + 1024;
const MAX_NODES = 100000, MAX_EDGES = 200000, MAX_OUTPUT = 8 * 1024 * 1024;
const SKIP = new Set(['loc', 'start', 'end', 'extra', 'comments', 'tokens', 'errors', 'leadingComments', 'trailingComments', 'innerComments',
  'typeAnnotation', 'returnType', 'typeParameters', 'typeArguments', 'superTypeParameters', 'superTypeArguments', 'implements', 'predicate']);
const RUNTIME_TS = new Set(['TSEnumDeclaration', 'TSEnumBody', 'TSEnumMember', 'TSModuleDeclaration', 'TSModuleBlock',
  'TSImportEqualsDeclaration', 'TSExternalModuleReference', 'TSExportAssignment', 'TSParameterProperty',
  'TSAsExpression', 'TSSatisfiesExpression', 'TSTypeAssertion', 'TSNonNullExpression', 'TSInstantiationExpression', 'TSQualifiedName']);
const fail = code => {const error = new Error(); error.publicCode = code; throw error;};

function program(source, kind) {
  const syntaxes = kind === 'typescript' ? [['typescript'], ['typescript', 'jsx']] : [['jsx']];
  for (const syntax of syntaxes) for (const sourceType of ['module', 'script']) {
    for (const decorator of ['decorators', 'decorators-legacy']) {
      try {return parse(source, {sourceType, plugins: [...syntax, decorator, 'decoratorAutoAccessors'], attachComment: false, errorRecovery: false}).program;}
      catch (error) {if (error instanceof RangeError) throw error;}
    }
  }
  fail('syntax');
}

function flatten(root) {
  const nodes = [], queue = [], ids = new WeakMap();
  let edges = 0;
  const isNode = value => value && typeof value === 'object' && typeof value.type === 'string';
  function reference(node) {
    if (!isNode(node) || (node.type.startsWith('TS') && !RUNTIME_TS.has(node.type))) return null;
    if (ids.has(node)) return ids.get(node);
    if (nodes.length >= MAX_NODES) throw new RangeError();
    const index = nodes.length;
    ids.set(node, index); queue.push(node); nodes.push(null);
    return index;
  }
  function scalar(fields, key, value) {
    if (typeof value === 'string' && value.length > 4096) fields['__omitted_' + key] = true;
    else if (typeof value === 'number' && !Number.isFinite(value)) fields['__omitted_' + key] = true;
    else if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) fields[key] = value;
  }
  reference(root);
  for (let index = 0; index < queue.length; index++) {
    const node = queue[index], fields = {}, children = {};
    for (const key of Object.keys(node)) {
      if (key === 'type' || SKIP.has(key)) continue;
      const value = node[key];
      if (isNode(value)) {children[key] = reference(value); edges++;}
      else if (Array.isArray(value)) {
        edges += value.length;
        if (edges > MAX_EDGES) throw new RangeError();
        children[key] = Array.from(value, reference);
      } else if (key === 'value' && node.type === 'TemplateElement' && value && typeof value === 'object') {
        scalar(fields, 'raw', value.raw); scalar(fields, 'cooked', value.cooked);
      } else scalar(fields, key, value);
    }
    if (edges > MAX_EDGES) throw new RangeError();
    nodes[index] = {type: node.type, line: node.loc?.start.line || 1, start: node.start, end: node.end, fields, children};
  }
  return nodes;
}

function document(request) {
  let root = null, result = null;
  try {
    if (typeof request.source !== 'string' || !['javascript', 'typescript'].includes(request.kind)) fail('invalid_input');
    if (Buffer.byteLength(request.source, 'utf8') > MAX_SOURCE) fail('input_limit');
    root = program(request.source, request.kind);
    result = {root: 0, nodes: flatten(root), parser: '8.0.4', source_type: root.sourceType};
    return result;
  } catch (error) {
    return {error: error.publicCode || (error instanceof RangeError ? 'limit' : 'failed')};
  } finally {root = null; result = null;}
}

let sequence = 0;
async function respond(buffer) {
  let request = null, result = null, frame = null;
  try {
    request = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(buffer));
    if (!request || request.v !== 1 || request.sequence !== sequence + 1
        || typeof request.nonce !== 'string' || !/^[0-9a-f]{32}$/.test(request.nonce)) return false;
    sequence++;
    result = document(request);
    frame = JSON.stringify({v: 1, nonce: request.nonce, sequence, pid: process.pid, document: result});
    if (Buffer.byteLength(frame, 'utf8') + 1 > MAX_OUTPUT) {
      frame = JSON.stringify({v: 1, nonce: request.nonce, sequence, pid: process.pid, document: {error: 'output_limit'}});
    }
    await new Promise((resolve, reject) => process.stdout.write(frame + '\n', error => error ? reject(error) : resolve()));
    return sequence < 128;
  } catch {return false;}
  finally {
    if (request) request.source = null;
    request = result = frame = buffer = null;
  }
}

// One bounded frame at a time. No AST or result cache; old heap contents are not secure erasure.
let parts = [], size = 0;
try {
  frames: for await (let chunk of process.stdin) {
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline < 0 ? chunk.length : newline;
      size += end - offset;
      if (size > MAX_INPUT) break frames;
      parts.push(chunk.subarray(offset, end));
      if (newline >= 0) {
        let buffer = Buffer.concat(parts, size);
        parts = []; size = 0;
        const keep = await respond(buffer);
        buffer = null;
        if (!keep) break frames;
      }
      offset = end + 1;
    }
    chunk = null;
  }
} catch {process.exitCode = 1;}
finally {parts = []; size = 0;}
