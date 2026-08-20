export type SaveState = "idle" | "saving" | "error";

export interface SaveEngineOptions {
  delayMs: number;
  /** Rejects on failure. The engine never interprets the reason. */
  send: (path: string, text: string) => Promise<void>;
  onStateChange?: (state: SaveState) => void;
}

/**
 * Debounced autosave for many open files at once.
 *
 * The single-file predecessor kept one timer, one pending string and one
 * in-flight promise in refs inside the workspace component. Every one of
 * those becomes a map keyed by path here -- and the module is pure so the
 * rules below can actually be tested, which they could not be inside a
 * component in a suite with no DOM.
 */
export class SaveEngine {
  private readonly opts: SaveEngineOptions;
  /** Latest not-yet-sent text, per path. */
  private pending = new Map<string, string>();
  private timers = new Map<string, ReturnType<typeof setTimeout>>();
  /** The send currently on the wire, per path. */
  private inFlight = new Map<string, Promise<void>>();
  /** Last text the server is known to hold, per path. */
  private baseline = new Map<string, string>();
  /** Paths whose last send REJECTED. Dirty until a later send succeeds. */
  private failed = new Set<string>();

  constructor(opts: SaveEngineOptions) {
    this.opts = opts;
  }

  setBaseline(path: string, text: string): void {
    this.baseline.set(path, text);
  }

  schedule(path: string, text: string): void {
    this.pending.set(path, text);
    const existing = this.timers.get(path);
    if (existing) clearTimeout(existing);
    this.timers.set(
      path,
      setTimeout(() => void this.flushPath(path), this.opts.delayMs)
    );
  }

  /**
   * Drop everything queued for a path. Used when the file is deleted: a
   * pending PUT against a path that no longer exists 404s, and that failure
   * surfaces as a "Could not save" banner under whatever file is open by
   * then.
   */
  forget(path: string): void {
    const timer = this.timers.get(path);
    if (timer) clearTimeout(timer);
    this.timers.delete(path);
    this.pending.delete(path);
    this.baseline.delete(path);
    this.failed.delete(path);
  }

  rename(from: string, to: string): void {
    const timer = this.timers.get(from);
    if (timer) clearTimeout(timer);
    this.timers.delete(from);
    if (this.pending.has(from)) {
      this.pending.set(to, this.pending.get(from) as string);
      this.pending.delete(from);
      this.timers.set(to, setTimeout(() => void this.flushPath(to), this.opts.delayMs));
    }
    if (this.baseline.has(from)) {
      this.baseline.set(to, this.baseline.get(from) as string);
      this.baseline.delete(from);
    }
    if (this.failed.delete(from)) this.failed.add(to);
  }

  /**
   * True the instant a key is pressed, and again from the moment a save
   * fails -- both are "the editor holds text the server does not".
   */
  isDirty(): boolean {
    return this.pending.size > 0 || this.inFlight.size > 0 || this.failed.size > 0;
  }

  /**
   * Which paths hold text the server does not. The tab bar renders a dot per
   * tab from this; `isDirty()` answers the same question for the whole
   * document and is what staleness uses. Both read the same three sets, so
   * they can never disagree.
   */
  dirtyPaths(): string[] {
    return [...new Set([...this.pending.keys(), ...this.inFlight.keys(), ...this.failed])];
  }

  async flushPath(path: string): Promise<void> {
    const timer = this.timers.get(path);
    if (timer) clearTimeout(timer);
    this.timers.delete(path);

    // SYNCHRONOUS capture-and-delete, above the first `await` on purpose:
    // this is what makes a second concurrent flush of the same path
    // incapable of sending the same edit twice. Do not move it below an
    // await.
    const text = this.pending.get(path);
    this.pending.delete(path);

    if (text === undefined) {
      // Nothing NEW was queued -- but an earlier flush may already have put
      // one on the wire. Wait for THAT, so a caller can rely on "the server
      // is caught up" when this resolves.
      await this.inFlight.get(path);
      return;
    }
    if (text === this.baseline.get(path)) return;

    this.opts.onStateChange?.("saving");
    const send = (async () => {
      try {
        await this.opts.send(path, text);
        this.baseline.set(path, text);
        this.failed.delete(path);
        this.opts.onStateChange?.("idle");
      } catch {
        // Deliberately swallowed and recorded rather than rethrown: a
        // failed autosave is a banner, not an unhandled rejection out of a
        // debounce timer.
        this.failed.add(path);
        this.opts.onStateChange?.("error");
      }
    })();
    this.inFlight.set(path, send);
    try {
      await send;
    } finally {
      // Only clear if this call's own send is still the one on record -- a
      // newer flush may already have replaced it with a later edit's send,
      // and clearing that would make a still-in-flight save look finished.
      if (this.inFlight.get(path) === send) this.inFlight.delete(path);
    }
  }

  /**
   * Every pending path, plus every send already on the wire.
   *
   * `compile()` awaits this: the backend compiles the SAVED tree, so
   * compiling mid-debounce builds the previous keystroke's text and returns
   * a SyncTeX map that does not match what is on screen. The union of both
   * key sets is taken BEFORE any await, so the set flushed is the set that
   * existed when the caller asked.
   */
  async flushAll(): Promise<void> {
    const paths = new Set([...this.pending.keys(), ...this.inFlight.keys()]);
    await Promise.all([...paths].map((p) => this.flushPath(p)));
  }

  dispose(): void {
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
  }
}
