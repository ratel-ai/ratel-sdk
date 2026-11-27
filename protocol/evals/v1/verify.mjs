#!/usr/bin/env node
// Reference verifier for the Ratel evals contract, v1.
//
// It checks each vector of conformance/vectors.json against its schema in schema/, with the
// validator of protocol/sandbox/v1 (Node built-ins only). A valid vector must pass. An invalid
// vector must fail, and its first error must be at the JSON pointer that the vector names.
//
//   node protocol/evals/v1/verify.mjs     exits 1 on the first mismatch

import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { validatorFor, verifyVectors } from '../../sandbox/v1/verify.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));

export const SCHEMAS = [
  'start-experiment.request', 'start-experiment.response', 'experiment',
  'pin-baseline.request', 'baseline', 'comparison', 'error',
];

export const validate = validatorFor(join(HERE, 'schema'));

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  verifyVectors({ label: 'evals/v1', vectorsFile: join(HERE, 'conformance', 'vectors.json'), schemas: SCHEMAS, validate });
}
