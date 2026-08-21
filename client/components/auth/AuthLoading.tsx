/**
 * Full-screen loading state shown while the auth bootstrap runs.
 *
 * Server Component — purely presentational, ships no JavaScript. Every part of
 * the animation is CSS, so it starts painting with the first frame rather than
 * waiting for a bundle to hydrate. That matters here more than anywhere else in
 * the app: this screen exists precisely because nothing has loaded yet.
 *
 * No visible caption. "Checking your session…" told the reader something they
 * could neither act on nor care about, and naming the internal step made a
 * fast bootstrap feel like a procedure being carried out. The spinner already
 * says "working"; the words only gave the eye something to read while waiting.
 *
 * `label` is kept and still announced — to a screen reader, a spinner alone is
 * silence, so the text moves to `sr-only` rather than being deleted.
 */
export function AuthLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      className="flex min-h-screen flex-col items-center justify-center bg-white dark:bg-slate-950"
      // Announced politely so a screen-reader user learns the app is working
      // rather than sitting on an apparently empty page.
      role="status"
      aria-live="polite"
    >
      <div className="relative h-20 w-20" aria-hidden="true">
        {/* The glow. Blurred and behind everything, so the loader sits in a
            pool of its own colour instead of floating on flat white. */}
        <span className="absolute inset-0 rounded-full bg-indigo-500/30 blur-xl motion-safe:[animation:loader-halo_2.8s_ease-in-out_infinite]" />

        {/* The track: a full ring at low contrast. Without it the moving arcs
            have nothing to travel along and the motion reads as two unrelated
            fragments rather than one rotation. */}
        <span className="absolute inset-0 rounded-full border-2 border-slate-200 dark:border-slate-800" />

        {/* Outer arc — three-quarters transparent, so what shows is a comet
            head rather than a spinning ring. */}
        <span className="absolute inset-0 rounded-full border-2 border-transparent border-t-indigo-500 border-r-indigo-500/30 motion-safe:animate-spin [animation-duration:1.1s]" />

        {/* Inner arc, slower and counter-rotating. The opposing direction is
            what gives the loader depth: two speeds in the same direction just
            look like one thing that cannot keep time. */}
        <span className="absolute inset-[0.6rem] rounded-full border-2 border-transparent border-b-sky-400 motion-safe:animate-spin [animation-direction:reverse] [animation-duration:1.7s]" />

        {/* The centre, breathing. Anchors the eye so the composition has a
            still point to rotate around. */}
        <span className="absolute inset-0 grid place-items-center">
          <span className="h-2 w-2 rounded-full bg-indigo-500 motion-safe:[animation:loader-breathe_2.8s_ease-in-out_infinite]" />
        </span>
      </div>

      <span className="sr-only">{label}</span>
    </div>
  );
}
