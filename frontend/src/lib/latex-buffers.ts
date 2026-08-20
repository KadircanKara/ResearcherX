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
  /**
   * The LATEST text queued on the wire for a path, so `rename` can re-queue
   * it under the new name. Sends for one path are serialised (see
   * `flushPath`), so this is exactly what the server will hold once
   * everything outstanding for the path has landed -- and re-queueing only
   * the latest is right, because anything earlier is superseded by it.
   */
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
    if (this.disposed) return;

    // ONE send outstanding per path, ever. A new send for a path CHAINS
    // behind whatever is already on the wire for it rather than racing it.
    //
    // The bug this closes: two overlapping PUTs to one endpoint, whose
    // response order the client does not control and the server does not
    // promise. Baseline "A"; the user types "B"; the debounce puts "B" on
    // the wire; the user undoes back to "A"; the next debounce put "A" on
    // the wire ALONGSIDE it. If "A" resolved first and "B" second, both
    // continuations wrote `baseline` and the last one won: `baseline` read
    // "B", the editor showed "A", `pending`/`inFlight`/`failed` were all
    // empty and `isDirty()` was false. Worse, WHICHEVER PUT THE SERVER
    // APPLIED LAST decided the file's real contents, and nothing on the
    // client could tell which that was.
    //
    // Note what merely INVALIDATING the superseded send would NOT have
    // fixed: both requests are already on the wire, so the server's final
    // state is still whichever it happened to apply last. Only serialising
    // them makes the server's end state deterministic -- and equal to the
    // last text the user typed, which is the only answer that can be
    // called correct. Widening the text comparison further, which is how
    // this class of bug got here, fixes neither half.
    //
    // `prev` is captured and `inFlight` is replaced SYNCHRONOUSLY, above
    // any await, so a third flush arriving while this one waits chains
    // behind THIS send rather than behind the one already landing.
    const prev = this.inFlight.get(path);
    // The text the server will hold once everything queued for this path
    // has landed -- true precisely because sends are serialised. `rename`
    // reads it to re-queue an edit that is already (or still) on the wire
    // under the old name.
    this.inFlightText.set(path, text);
    const send = (async () => {
      if (prev) {
        // `prev` never rejects -- the catch below is inside it -- so this
        // resolves whether the earlier save succeeded or failed.
        await prev;
        // A `rename`/`forget` during the wait means this send is about a
        // path that no longer means what it meant. Its text was already
        // re-queued under the new name by `rename`; sending it here would
        // PUT to a path the server no longer has.
        if (this.obsolete(path, epoch) || this.disposed) return;
      }
      // Nothing is on the wire for this path now, so `baseline` really is
      // what the server holds and is the correct comparand again. Skipping
      // here rather than before the chain is what keeps a revert from being
      // dropped: with the earlier send resolved, "A" is compared against a
      // baseline that has caught up to "B" and is correctly sent.
      //
      // `failed` vetoes the skip: after a rejected save the server does NOT
      // hold `baseline`'s text for this path, and the redundant-looking
      // write is the only thing that clears the flag.
      if (text === this.baseline.get(path) && !this.failed.has(path)) return;
      // Announced HERE, not when the flush was queued: a chained send that
      // is only waiting its turn is already covered by the "saving" the
      // send ahead of it announced, and a send that turns out to be
      // redundant should not flicker the badge through saving/idle at all.
      this.opts.onStateChange?.("saving", path);
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
