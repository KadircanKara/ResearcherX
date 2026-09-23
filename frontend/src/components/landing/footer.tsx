import { LogoMark } from "./logo-mark";

export function Footer() {
  return (
    <footer className="border-site-line border-t">
      <div className="text-site-muted mx-auto flex max-w-[1180px] flex-col gap-3 px-5 py-8 text-[13px] sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <span className="text-site-fg inline-flex items-center gap-2 font-semibold">
          <LogoMark />
          ResearcherX
        </span>
        <span>
          © {new Date().getFullYear()} Kadircan Kara · MIT licensed ·{" "}
          <a
            href="https://github.com/KadircanKara/ResearcherX"
            target="_blank"
            rel="noreferrer"
            className="hover:text-site-fg underline underline-offset-4"
          >
            GitHub
          </a>
        </span>
      </div>
    </footer>
  );
}
