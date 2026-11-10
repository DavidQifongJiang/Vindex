const checksums = new WeakMap<File, Promise<string>>();

// Hash off the UI thread, with bounded reads. Reuse the digest when retrying
// the same File object; failed workers are evicted so the next retry can work.
export function sha256File(file: File): Promise<string> {
  const existing = checksums.get(file);
  if (existing) return existing;
  const result = new Promise<string>((resolve, reject) => {
    const worker = new Worker(new URL("./uploadHash.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = ({ data }: MessageEvent<{ sha256?: string; error?: string }>) => {
      worker.terminate();
      if (data.sha256) resolve(data.sha256);
      else reject(new Error("Could not verify this file. Please try again."));
    };
    worker.onerror = () => {
      worker.terminate();
      reject(new Error("Could not verify this file. Please try again."));
    };
    worker.postMessage(file);
  }).catch(error => {
    checksums.delete(file);
    throw error;
  });
  checksums.set(file, result);
  return result;
}
