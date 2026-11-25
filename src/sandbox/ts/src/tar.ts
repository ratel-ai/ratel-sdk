import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";

export type TarFile = {
  /** The path in the bundle, with `/`. */
  name: string;
  /** The file to read. */
  path: string;
  bytes: number;
  executable: boolean;
};

const BLOCK = 512;

/** Writes the files as a gzip tar. The same files give the same bytes. */
export async function writeTarGz(
  files: readonly TarFile[],
  to: string,
): Promise<{ sha256: string; bytes: number }> {
  const hash = createHash("sha256");
  let bytes = 0;
  const count = new Transform({
    transform(chunk: Buffer, _encoding, done) {
      hash.update(chunk);
      bytes += chunk.length;
      done(null, chunk);
    },
  });
  await pipeline(Readable.from(blocks(files)), createGzip(), count, createWriteStream(to));
  return { sha256: hash.digest("hex"), bytes };
}

async function* blocks(files: readonly TarFile[]): AsyncGenerator<Buffer> {
  for (const file of files) {
    const name = Buffer.from(file.name);
    if (name.length > 100) {
      const record = paxRecord("path", file.name);
      yield header("pax", record.length, "x", false);
      yield record;
      yield padding(record.length);
    }
    yield header(
      name.length > 100 ? name.subarray(0, 100) : name,
      file.bytes,
      "0",
      file.executable,
    );
    let read = 0;
    for await (const chunk of createReadStream(file.path)) {
      read += (chunk as Buffer).length;
      yield chunk as Buffer;
    }
    if (read !== file.bytes) throw new Error(`${file.name} changed during the push. Push again.`);
    yield padding(read);
  }
  yield Buffer.alloc(BLOCK * 2);
}

function header(name: string | Buffer, size: number, type: string, executable: boolean): Buffer {
  const block = Buffer.alloc(BLOCK);
  Buffer.from(name).copy(block, 0, 0, 100);
  block.write(executable ? "0000755\0" : "0000644\0", 100);
  block.write("0000000\0", 108);
  block.write("0000000\0", 116);
  block.write(`${size.toString(8).padStart(11, "0")}\0`, 124);
  block.write("00000000000\0", 136);
  block.write("        ", 148);
  block.write(type, 156);
  block.write("ustar\x0000", 257);
  const sum = block.reduce((total, byte) => total + byte, 0);
  block.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148);
  return block;
}

/** A record has its own length in it. */
function paxRecord(key: string, value: string): Buffer {
  const rest = Buffer.byteLength(` ${key}=${value}\n`);
  let length = rest;
  while (String(length).length + rest !== length) length = String(length).length + rest;
  return Buffer.from(`${length} ${key}=${value}\n`);
}

function padding(bytes: number): Buffer {
  return Buffer.alloc((BLOCK - (bytes % BLOCK)) % BLOCK);
}
