export type SaveState = "idle" | "saving" | "error";

export interface SaveEngineOptions {
  delayMs: number;
  /** Rejects on failure. The engine never interprets the reason. */
  send: (path: string, text: string) => Promise<void>;
  /**
   * `path` is carried because a save failure is a fact about ONE FILE. The
   * consumer's failure record is keyed by path for exactly the reason the
   * engine's own `failed` set is: a successful save of some other file is no
   * evidence at all about this one.
   */
  onStateChange?: (state: SaveState, path: string) => void;
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
  /**
   * Bumped for a path whenever `rename` or `forget` makes an in-flight send
   * for it obsolete. A send captures the epoch it started under and refuses
   * to write `baseline`/`failed`/state if the value has moved since -- its
   * result is about a path that no longer means what it meant.
   */
  private epoch = new Map<string, number>();
  /** The text each in-flight send is carrying, so `rename` can re-queue it. */
  private inFlightText = new Map<string, string>();
  private disposed = false;

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
    this.invalidate(path);
    const timer = this.timers.get(path);
    if (timer) clearTimeout(timer);
    this.timers.delete(path);
    this.pending.delete(path);
    this.baseline.delete(path);
    this.failed.delete(path);
  }

  rename(from: string, to: string): void {
    const flying = this.inFlightText.get(from);
    this.invalidate(from);
    const timer = this.timers.get(from);
    if (timer) clearTimeout(timer);
    this.timers.delete(from);
    if (this.pending.has(from)) {
      this.pending.set(to, this.pending.get(from) as string);
      this.pending.delete(from);
    } else if (flying !== undefined) {
      // The edit is already on the wire under the OLD name and its result is
      // now ignored, so the only way the user's text reaches the file is to
      // send it again under the new one. Without this the text is lost with
      // the engine reporting clean.
      this.pending.set(to, flying);
    }
    if (this.pending.has(to)) {
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

  private obsolete(path: string, epoch: number): boolean {
    return this.disposed || (this.epoch.get(path) ?? 0) !== epoch;
  }

  /**
   * Make any in-flight send for `path` a no-op and forget it. The network
   * call cannot be recalled -- what this guarantees is that its RESULT is
   * never believed, so it can neither resurrect a baseline for a path that
   * has moved nor make a renamed file look clean.
   */
  private invalidate(path: string): void {
    this.epoch.set(path, (this.epoch.get(path) ?? 0) + 1);
    this.inFlight.delete(path);
    this.inFlightText.delete(path);
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
    const epoch = this.epoch.get(path) ?? 0;

    if (text === undefined) {
      // Nothing NEW was queued -- but an earlier flush may already have put
      // one on the wire. Wait for THAT, so a caller can rely on "the server
      // is caught up" when this resolves.
      await this.inFlight.get(path);
      return;
    }
    // Skip the send only when the text already matches WHAT THE SERVER IS
    // ABOUT TO HOLD -- which is `baseline` only while nothing is on the wire
    // for this path.
    //
    // The naive `text === baseline` check was written when `baseline` really
    // was the server's state; the in-flight bookkeeping added later
    // (`inFlight`/`inFlightText`/`epoch`) made it a LAGGING record for the
    // duration of a send. The failure it caused: baseline "A", the user
    // types "B", the debounce sends "B", and inside that round trip the user
    // undoes back to "A". The next flush compared "A" against a baseline
    // still reading "A" -- because "B" had not resolved yet -- and returned
    // without sending. Then "B" resolved and set baseline to "B". End state:
    // the editor showed "A", the server held "B", `pending`/`inFlight`/
    // `failed` were all empty, `isDirty()` was false, and the next compile
    // built "B" and reported itself up to date. The user's undo was
    // discarded and the version they explicitly reverted is what compiled
    // and exported.
    //
    // While a send IS in flight, `inFlightText` is what the server will hold
    // once it lands, so that is the only correct comparand. If that send
    // ultimately FAILS the extra write is redundant rather than wrong -- and
    // it is what clears the `failed` flag.
    const flying = this.inFlightText.get(path);
    if (text === (flying !== undefined ? flying : this.baseline.get(path))) return;

    if (this.disposed) return;
    this.opts.onStateChange?.("saving", path);
    this.inFlightText.set(path, text);
    const send = (async () => {
      try {
        await this.opts.send(path, text);
        if (this.obsolete(path, epoch)) return;
        this.baseline.set(path, text);
        this.failed.delete(path);
        this.opts.onStateChange?.("idle", path);
      } catch {
        // Deliberately swallowed and recorded rather than rethrown: a
        // failed autosave is a banner, not an unhandled rejection out of a
        // debounce timer.
        if (this.obsolete(path, epoch)) return;
        this.failed.add(path);
        this.opts.onStateChange?.("error", path);
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
      if (this.inFlightText.get(path) === text) this.inFlightText.delete(path);
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
    // Loop rather than snapshot once. `rename` can requeue a carried
    // in-flight edit under a NEW path AFTER this call took its snapshot,
    // on a fresh debounce timer this call never saw. `compile()` awaits
    // this and then asks the backend to build immediately, so a
    // single-snapshot flushAll lets a rename racing a compile build the
    // pre-edit text -- precisely the hazard this method exists to close.
    //
    // Terminates: a send that fails does NOT return its text to `pending`
    // (it goes to `failed`), so no pass can re-add work by itself. Only a
    // concurrent rename adds a pass, and renames are user-driven and
    // finite. The bound is a safety valve against a caller looping renames
    // faster than sends settle, not an expected exit.
    //
    // At the bound this resolves with work still pending, silently to a
    // caller inspecting only the promise. That is survivable rather than
    // correct: `isDirty()` keeps reporting the path dirty, its own debounce
    // still sends it, and a compile that races it is marked stale by the
    // revision rule instead of presented as fresh -- so the cost is a wasted
    // compile, never a wrong PDF. Reaching it needs ten renames of the same
    // in-flight path inside one flush, which is an adversarial burst rather
    // than a user's editing session.
    for (let pass = 0; pass < 10; pass += 1) {
      const paths = new Set([...this.pending.keys(), ...this.inFlight.keys()]);
      if (paths.size === 0) return;
      await Promise.all([...paths].map((p) => this.flushPath(p)));
    }
  }

  dispose(): void {
    this.disposed = true;
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
  }
}
