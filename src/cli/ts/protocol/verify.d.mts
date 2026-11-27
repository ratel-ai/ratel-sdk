// Types of verify.mjs: a copy of protocol/sandbox/v1. Do not edit the copy: run
// `pnpm sync-protocol`. A test fails when the copy differs from the protocol.

/** A violation of the contract, at a JSON pointer. */
export interface ContractError {
  path: string;
  message: string;
}

export declare const SCHEMAS: readonly string[];
export declare function validate(schemaName: string, doc: unknown): ContractError[];
export declare function ratelYamlRules(doc: unknown): ContractError[];
export declare function validatorFor(schemaDir: string): (schemaName: string, doc: unknown) => ContractError[];
export declare function verifyVectors(options: {
  label: string;
  vectorsFile: string;
  schemas: readonly string[];
  validate: (schemaName: string, doc: unknown) => ContractError[];
}): void;
