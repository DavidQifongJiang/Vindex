import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

self.onmessage = async ({ data: file }: MessageEvent<File>) => {
  const hash = sha256.create();
  try {
    const chunkBytes = 1024 * 1024;
    for (let offset = 0; offset < file.size; offset += chunkBytes) {
      hash.update(new Uint8Array(await file.slice(offset, offset + chunkBytes).arrayBuffer()));
    }
    self.postMessage({ sha256: bytesToHex(hash.digest()) });
  } catch {
    self.postMessage({ error: "File checksum failed" });
  } finally {
    hash.destroy();
  }
};
