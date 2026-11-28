#!/usr/bin/env node
import { setTimeout as sleep } from "node:timers/promises";
import { localDocker } from "./docker.js";
import { main } from "./main.js";

main(process.argv.slice(2), {
  env: process.env,
  stdout: (text) => process.stdout.write(text),
  stderr: (text) => process.stderr.write(text),
  docker: localDocker(),
  sleep: (ms) => sleep(ms),
}).then(
  (code) => {
    process.exitCode = code;
  },
  (error: unknown) => {
    process.stderr.write(`ratel: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  },
);
