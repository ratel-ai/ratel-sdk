#!/usr/bin/env node
// Reference verifier for the Ratel sandbox contract, v1.
//
// It checks each vector of conformance/vectors.json against its schema in
// schema/. A valid vector must pass. An invalid vector must fail, and its
// first error must be at the JSON pointer that the vector names.
//
//   node protocol/sandbox/v1/verify.mjs     exits 1 on the first mismatch
//
// The schemas are normative. This file is a small JSON Schema validator for
// the keywords that the schemas use, with Node built-ins only. It refuses a
// keyword that it does not know, so a schema cannot use a rule that this file
// silently skips. The SDK and the CLI import `validate` from here, and the
// verifier of protocol/evals/v1 imports `validatorFor` and `verifyVectors`.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

export const SCHEMAS = [
  'run-request', 'run-response', 'error', 'ratel-yaml',
  'api-error', 'create-version.request', 'create-version.response', 'version', 'start-run.request', 'sandbox-run',
];

const CARRIES_CONFIG = new Set(['create-version.request', 'version']);

// `format` is an annotation in JSON Schema 2020-12: it is not checked.
const ANNOTATIONS = new Set([
  '$schema', '$id', '$defs', '$comment', 'title', 'description', 'default', 'examples', 'format',
]);

const validateSandbox = validatorFor(join(HERE, 'schema'));

// Validate a document against one schema of SCHEMAS. Returns a list of
// errors `{ path, message }`, where path is a JSON pointer. Empty: valid.
export function validate(schemaName, doc) {
  const errors = validateSandbox(schemaName, doc);
  if (errors.length === 0) {
    if (schemaName === 'ratel-yaml') errors.push(...ratelYamlRules(doc));
    else if (CARRIES_CONFIG.has(schemaName) && doc.config) {
      errors.push(...ratelYamlRules(doc.config).map((e) => ({ ...e, path: `/config${e.path}` })));
    }
  }
  return errors;
}

// Rules of ratel.yaml that JSON Schema cannot say.
export function ratelYamlRules(doc) {
  const errors = [];
  for (const [name, param] of Object.entries(doc.params ?? {})) {
    if (param.type === 'enum' && 'default' in param && !param.options.includes(param.default)) {
      errors.push({ path: `/params/${esc(name)}/default`, message: 'must be one of options' });
    }
  }
  return errors;
}

// A validator of the schemas `<name>.schema.json` in a folder.
export function validatorFor(schemaDir) {
  const loaded = new Map();
  const load = (file) => {
    if (!loaded.has(file)) loaded.set(file, JSON.parse(readFileSync(join(schemaDir, file), 'utf8')));
    return loaded.get(file);
  };
  return (schemaName, doc) => {
    const root = load(`${schemaName}.schema.json`);
    const errors = [];
    check(root, doc, '', { root, errors, load });
    return errors;
  };
}

function check(schema, value, path, ctx) {
  if (schema === true) return;
  if (schema === false) return fail(ctx, path, 'is not allowed');
  for (const key of Object.keys(schema)) {
    if (ANNOTATIONS.has(key)) continue;
    const rule = KEYWORDS[key];
    if (!rule) throw new Error(`verify.mjs does not support the keyword "${key}"`);
    rule(schema[key], value, path, ctx, schema);
  }
}

const isObj = (x) => typeof x === 'object' && x !== null && !Array.isArray(x);

const TYPES = {
  object: isObj,
  array: Array.isArray,
  string: (x) => typeof x === 'string',
  number: (x) => typeof x === 'number' && Number.isFinite(x),
  integer: (x) => Number.isInteger(x),
  boolean: (x) => typeof x === 'boolean',
  null: (x) => x === null,
};

const KEYWORDS = {
  $ref(ref, value, path, ctx) {
    const [file, pointer = ''] = ref.split('#');
    const root = file ? ctx.load(file) : ctx.root;
    const target = pointer.split('/').slice(1).reduce((node, part) => node[unesc(part)], root);
    if (target === undefined) throw new Error(`unresolved $ref ${ref}`);
    check(target, value, path, { ...ctx, root });
  },
  type(types, value, path, ctx) {
    const list = Array.isArray(types) ? types : [types];
    if (!list.some((t) => TYPES[t](value))) fail(ctx, path, `must be ${list.join(' or ')}`);
  },
  const(expected, value, path, ctx) {
    if (!equal(expected, value)) fail(ctx, path, `must be ${JSON.stringify(expected)}`);
  },
  enum(options, value, path, ctx) {
    if (!options.some((o) => equal(o, value))) {
      fail(ctx, path, `must be one of ${options.map((o) => JSON.stringify(o)).join(', ')}`);
    }
  },
  required(names, value, path, ctx) {
    if (!isObj(value)) return;
    for (const name of names) if (!(name in value)) fail(ctx, `${path}/${esc(name)}`, 'is required');
  },
  properties(props, value, path, ctx) {
    if (!isObj(value)) return;
    for (const [name, sub] of Object.entries(props)) {
      if (name in value) check(sub, value[name], `${path}/${esc(name)}`, ctx);
    }
  },
  additionalProperties(sub, value, path, ctx, schema) {
    if (!isObj(value)) return;
    const known = new Set(Object.keys(schema.properties ?? {}));
    for (const name of Object.keys(value)) {
      if (known.has(name)) continue;
      if (sub === false) fail(ctx, `${path}/${esc(name)}`, 'is not a known field');
      else check(sub, value[name], `${path}/${esc(name)}`, ctx);
    }
  },
  propertyNames(sub, value, path, ctx) {
    if (!isObj(value)) return;
    for (const name of Object.keys(value)) {
      if (!passes(sub, name, ctx)) fail(ctx, `${path}/${esc(name)}`, 'is not a valid name');
    }
  },
  items(sub, value, path, ctx) {
    if (!Array.isArray(value)) return;
    value.forEach((item, i) => check(sub, item, `${path}/${i}`, ctx));
  },
  minItems(n, value, path, ctx) {
    if (Array.isArray(value) && value.length < n) fail(ctx, path, `must have at least ${n} items`);
  },
  uniqueItems(on, value, path, ctx) {
    if (!on || !Array.isArray(value)) return;
    value.forEach((item, i) => {
      if (value.slice(0, i).some((prev) => equal(prev, item))) fail(ctx, `${path}/${i}`, 'is a duplicate');
    });
  },
  minLength(n, value, path, ctx) {
    if (typeof value === 'string' && [...value].length < n) fail(ctx, path, `must have at least ${n} characters`);
  },
  maxLength(n, value, path, ctx) {
    if (typeof value === 'string' && [...value].length > n) fail(ctx, path, `must have at most ${n} characters`);
  },
  pattern(re, value, path, ctx) {
    if (typeof value === 'string' && !new RegExp(re, 'u').test(value)) fail(ctx, path, `must match ${re}`);
  },
  minimum(n, value, path, ctx) {
    if (typeof value === 'number' && value < n) fail(ctx, path, `must be >= ${n}`);
  },
  maximum(n, value, path, ctx) {
    if (typeof value === 'number' && value > n) fail(ctx, path, `must be <= ${n}`);
  },
  allOf(subs, value, path, ctx) {
    for (const sub of subs) check(sub, value, path, ctx);
  },
  anyOf(subs, value, path, ctx) {
    if (!subs.some((sub) => passes(sub, value, ctx))) {
      // Report the errors of the first branch: they name the missing field.
      check(subs[0], value, path, ctx);
    }
  },
  oneOf(subs, value, path, ctx) {
    const matches = subs.filter((sub) => passes(sub, value, ctx)).length;
    if (matches === 1) return;
    if (matches > 1) return fail(ctx, path, 'matches more than one form');
    // Report the branch with the fewest errors: it is the form the author meant.
    const best = subs
      .map((sub) => collect(sub, value, path, ctx))
      .sort((a, b) => a.length - b.length)[0];
    ctx.errors.push(...best);
  },
  not(sub, value, path, ctx) {
    if (passes(sub, value, ctx)) fail(ctx, path, 'is not allowed here');
  },
  if(cond, value, path, ctx, schema) {
    if (passes(cond, value, ctx)) {
      if (schema.then) check(schema.then, value, path, ctx);
    } else if (schema.else) {
      check(schema.else, value, path, ctx);
    }
  },
  then() {},
  else() {},
};

function collect(schema, value, path, ctx) {
  const errors = [];
  check(schema, value, path, { ...ctx, errors });
  return errors;
}

function passes(schema, value, ctx) {
  return collect(schema, value, '', ctx).length === 0;
}

function fail(ctx, path, message) {
  ctx.errors.push({ path, message });
}

function equal(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function esc(part) {
  return String(part).replaceAll('~', '~0').replaceAll('/', '~1');
}

function unesc(part) {
  return part.replaceAll('~1', '/').replaceAll('~0', '~');
}

// ---- conformance -----------------------------------------------------------

// Check each vector of a vectors.json against its schema. It exits 1 on a mismatch.
export function verifyVectors({ label, vectorsFile, schemas, validate: check }) {
  const { vectors } = JSON.parse(readFileSync(vectorsFile, 'utf8'));
  const seen = new Map(schemas.map((s) => [s, { valid: 0, invalid: 0 }]));
  const failures = [];
  for (const v of vectors) {
    if (!seen.has(v.schema)) {
      failures.push(`${v.name}: unknown schema ${v.schema}`);
      continue;
    }
    const errors = check(v.schema, v.document);
    if (v.valid) {
      seen.get(v.schema).valid++;
      if (errors.length > 0) failures.push(`${v.name}: expected valid, got ${fmt(errors)}`);
    } else {
      seen.get(v.schema).invalid++;
      if (errors.length === 0) failures.push(`${v.name}: expected invalid at ${v.path}, got valid`);
      else if (errors[0].path !== v.path) failures.push(`${v.name}: expected error at ${v.path}, got ${fmt(errors)}`);
    }
  }
  for (const [schema, n] of seen) {
    if (n.valid === 0 || n.invalid === 0) failures.push(`${schema}: needs at least one valid and one invalid vector`);
  }
  if (failures.length > 0) {
    for (const f of failures) console.error(`FAIL ${f}`);
    process.exit(1);
  }
  console.log(`${label}: ${vectors.length} vectors pass (${[...seen].map(([s, n]) => `${s} ${n.valid}+${n.invalid}`).join(', ')})`);
}

function fmt(errors) {
  return errors.map((e) => `${e.path || '/'} ${e.message}`).join('; ');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  verifyVectors({ label: 'sandbox/v1', vectorsFile: join(HERE, 'conformance', 'vectors.json'), schemas: SCHEMAS, validate });
}
