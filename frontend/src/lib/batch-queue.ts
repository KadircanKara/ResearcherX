/** Bounded-concurrency runner for batch paper operations.
 *
 * Concurrency of 3 mirrors the backend's max_parallel_searchers=3 and stays
 * well inside Groq's ~30 req/min. Never rejects: a worker that throws is the
 * worker's problem to record on its own item, so one bad file cannot abort
 * the rest of the batch.
 */
export async function runBatch<T>(
  items: T[],
  worker: (item: T, index: number) => Promise<void>,
  concurrency = 3
): Promise<void> {
  let cursor = 0;
  const lanes = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      try {
        await worker(items[index], index);
      } catch {
        // Swallowed by contract — the worker records failure on its own item.
      }
    }
  });
  await Promise.all(lanes);
}
