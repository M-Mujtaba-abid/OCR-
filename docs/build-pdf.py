#!/usr/bin/env python3
"""Render the architecture blueprint markdown to print-ready PDFs.

Usage
-----
    python docs/build-pdf.py                # build everything into docs/pdf/
    python docs/build-pdf.py --keep-html    # also leave the intermediate HTML

Why this exists
---------------
There is no pandoc / wkhtmltopdf on the target machine, and Word COM mangles
fenced code blocks. Chrome (or Edge) in headless mode renders the same engine
that produced the HTML, so what you see in a browser is what lands in the PDF.

Dependencies: `markdown` and `pygments` (both pure Python). Install them into a
throwaway venv so the project's own venv stays clean:

    py -3.12 -m venv .docvenv
    .docvenv\\Scripts\\python.exe -m pip install markdown pygments
    .docvenv\\Scripts\\python.exe docs/build-pdf.py
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import markdown
    from pygments.formatters import HtmlFormatter
except ImportError:  # pragma: no cover
    sys.exit(
        "Missing dependencies. Run:\n"
        "    py -3.12 -m venv .docvenv\n"
        "    .docvenv\\Scripts\\python.exe -m pip install markdown pygments\n"
        "    .docvenv\\Scripts\\python.exe docs/build-pdf.py"
    )

DOCS_DIR = Path(__file__).resolve().parent
OUT_DIR = DOCS_DIR / "pdf"

TITLE = "AP Invoice-to-PO Automation"
SUBTITLE = "Production Architecture Blueprint \u2014 v1 (MVP)"

# (filename stem, display title) in reading order.
SECTIONS: list[tuple[str, str]] = [
    ("00-overview", "Overview & Decisions"),
    ("01-backend-architecture", "Backend Architecture"),
    ("02-database-schema", "Database Schema"),
    ("03-services", "Core Service Integrations"),
    ("04-api-contract", "API Contract"),
    ("05-frontend-architecture", "Frontend Architecture"),
    ("06-setup-runbook", "Setup & Runbook"),
]

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

MD_EXTENSIONS = [
    "fenced_code",
    "codehilite",
    "tables",
    "toc",
    "attr_list",
    "sane_lists",
    "md_in_html",
]
MD_EXTENSION_CONFIGS = {
    # Inline <style> from Pygments rather than CSS classes on a stylesheet we
    # would then have to ship alongside the HTML.
    "codehilite": {"guess_lang": False, "noclasses": False},
    "toc": {"permalink": False},
}

# --------------------------------------------------------------------------- CSS
# Everything is inlined: no webfonts, no CDN. Chrome renders offline and
# deterministically, and the HTML files stay self-contained if shared.
BASE_CSS = """
@page {
  size: A4;
  margin: 18mm 16mm 20mm 16mm;
}

:root {
  --ink: #16181d;
  --muted: #5b6472;
  --rule: #d8dde5;
  --accent: #1f4e79;
  --code-bg: #f6f8fa;
  --code-border: #e2e6ec;
}

* { box-sizing: border-box; }

body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 10.2pt;
  line-height: 1.55;
  color: var(--ink);
  margin: 0;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

/* ---- headings ---------------------------------------------------------- */
h1, h2, h3, h4 {
  color: var(--accent);
  font-weight: 600;
  line-height: 1.25;
  break-after: avoid;
  page-break-after: avoid;
}
h1 {
  font-size: 21pt;
  margin: 0 0 14pt;
  padding-bottom: 6pt;
  border-bottom: 2px solid var(--accent);
}
h2 {
  font-size: 15pt;
  margin: 20pt 0 8pt;
  padding-bottom: 3pt;
  border-bottom: 1px solid var(--rule);
}
h3 { font-size: 12pt; margin: 15pt 0 6pt; }
h4 { font-size: 10.6pt; margin: 12pt 0 4pt; color: var(--ink); }

p { margin: 0 0 8pt; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 8pt; padding-left: 20pt; }
li { margin-bottom: 3pt; }

a { color: var(--accent); text-decoration: none; }

hr {
  border: 0;
  border-top: 1px solid var(--rule);
  margin: 16pt 0;
}

/* ---- code -------------------------------------------------------------- */
code, kbd, pre, tt {
  font-family: "Cascadia Mono", Consolas, "SF Mono", "Liberation Mono", monospace;
}

/* Inline code */
p code, li code, td code, h2 code, h3 code, h4 code, a code {
  background: var(--code-bg);
  border: 1px solid var(--code-border);
  border-radius: 3px;
  padding: 0.5pt 3pt;
  font-size: 0.88em;
  /* Long dotted paths (app.services.matching_engine) must not overflow. */
  word-break: break-word;
}

/* Fenced blocks. `break-inside: avoid` keeps short blocks whole; very long
   blocks are allowed to split rather than leaving a page nearly empty. */
div.codehilite, pre {
  background: var(--code-bg);
  border: 1px solid var(--code-border);
  border-left: 3px solid var(--accent);
  border-radius: 4px;
  margin: 0 0 10pt;
  padding: 7pt 9pt;
  font-size: 7.6pt;
  line-height: 1.42;
  break-inside: avoid;
  page-break-inside: avoid;
}
div.codehilite pre {
  background: none;
  border: 0;
  margin: 0;
  padding: 0;
}
div.codehilite.long, div.codehilite.long pre {
  break-inside: auto;
  page-break-inside: auto;
}
pre code {
  background: none;
  border: 0;
  padding: 0;
  font-size: inherit;
  /* The critical rule: wrap instead of clipping at the right margin. */
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

/* ---- tables ------------------------------------------------------------ */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0 0 10pt;
  font-size: 8.8pt;
  break-inside: avoid;
  page-break-inside: avoid;
}
thead { display: table-header-group; }
th, td {
  border: 1px solid var(--rule);
  padding: 4pt 6pt;
  text-align: left;
  vertical-align: top;
}
th { background: #eef2f7; font-weight: 600; color: var(--accent); }
tr:nth-child(even) td { background: #fafbfc; }
td code, th code { font-size: 0.86em; }

blockquote {
  margin: 0 0 10pt;
  padding: 5pt 10pt;
  border-left: 3px solid #f0b429;
  background: #fffaf0;
  color: #5c4813;
}
blockquote p:last-child { margin-bottom: 0; }

strong { font-weight: 600; }
"""

COVER_CSS = """
.cover {
  break-after: page;
  page-break-after: always;
  height: 245mm;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.cover .eyebrow {
  font-size: 9pt;
  letter-spacing: 2.2pt;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 10pt;
}
.cover h1 {
  font-size: 33pt;
  line-height: 1.12;
  border: 0;
  margin: 0 0 10pt;
  padding: 0;
}
.cover .subtitle {
  font-size: 13pt;
  color: var(--muted);
  margin-bottom: 22pt;
}
.cover .rule {
  width: 70pt;
  border-top: 3px solid var(--accent);
  margin-bottom: 22pt;
}
.cover dl {
  margin: 0;
  font-size: 9.6pt;
  display: grid;
  grid-template-columns: 34mm 1fr;
  row-gap: 5pt;
}
.cover dt { color: var(--muted); }
.cover dd { margin: 0; font-weight: 500; }

.toc-page { break-after: page; page-break-after: always; }
.toc-list { list-style: none; padding: 0; font-size: 11pt; }
.toc-list li {
  padding: 6pt 0;
  border-bottom: 1px dotted var(--rule);
}
.toc-list .num {
  display: inline-block;
  width: 26pt;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.section-start { break-before: page; page-break-before: always; }
"""


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    sys.exit(
        "No Chrome or Edge found. Install one, or set CHROME_CANDIDATES in this script."
    )


def make_converter() -> markdown.Markdown:
    return markdown.Markdown(
        extensions=MD_EXTENSIONS, extension_configs=MD_EXTENSION_CONFIGS
    )


def render_markdown(md_path: Path) -> str:
    md = make_converter()
    body = md.convert(md_path.read_text(encoding="utf-8"))
    # Tag oversized code blocks so they may split across pages instead of
    # pushing a whole near-empty page ahead of themselves.
    return re.sub(
        r'<div class="codehilite">((?:(?!</div>).)*?)</div>',
        lambda m: (
            f'<div class="codehilite long">{m.group(1)}</div>'
            if m.group(1).count("\n") > 45
            else m.group(0)
        ),
        body,
        flags=re.DOTALL,
    )


def page_shell(title: str, body: str, extra_css: str = "") -> str:
    pygments_css = HtmlFormatter(style="friendly").get_style_defs(".codehilite")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
{BASE_CSS}
{extra_css}
{pygments_css}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def build_cover_and_toc() -> str:
    from datetime import date

    rows = "\n".join(
        f'<li><span class="num">{i:02d}</span>{html.escape(title)}</li>'
        for i, (_, title) in enumerate(SECTIONS)
    )
    return f"""
<section class="cover">
  <div class="eyebrow">Technical Blueprint</div>
  <h1>{html.escape(TITLE)}</h1>
  <div class="subtitle">{html.escape(SUBTITLE)}</div>
  <div class="rule"></div>
  <dl>
    <dt>Stack</dt><dd>FastAPI &middot; PostgreSQL &middot; Odoo XML-RPC &middot; Mistral OCR &middot; Next.js</dd>
    <dt>Audience</dt><dd>Implementing engineers</dd>
    <dt>Status</dt><dd>Approved for implementation</dd>
    <dt>Generated</dt><dd>{date.today().isoformat()}</dd>
  </dl>
</section>

<section class="toc-page">
  <h1>Contents</h1>
  <ul class="toc-list">
{rows}
  </ul>
</section>
"""


def print_to_pdf(browser: str, html_path: Path, pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        "--virtual-time-budget=8000",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]
    with tempfile.TemporaryDirectory() as profile:
        cmd.insert(1, f"--user-data-dir={profile}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        sys.exit(
            f"Chrome produced no PDF for {html_path.name}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-html", action="store_true", help="leave intermediate HTML in docs/pdf/"
    )
    args = parser.parse_args()

    browser = find_browser()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Browser: {browser}")

    combined_parts: list[str] = [build_cover_and_toc()]
    work_dir = OUT_DIR if args.keep_html else Path(tempfile.mkdtemp(prefix="apdocs-"))

    for index, (stem, title) in enumerate(SECTIONS):
        md_path = DOCS_DIR / f"{stem}.md"
        if not md_path.exists():
            print(f"  ! skipping missing {md_path.name}")
            continue

        body = render_markdown(md_path)

        html_path = work_dir / f"{stem}.html"
        html_path.write_text(page_shell(title, body), encoding="utf-8")
        pdf_path = OUT_DIR / f"{stem}.pdf"
        print_to_pdf(browser, html_path, pdf_path)
        size_kb = pdf_path.stat().st_size / 1024
        print(f"  [{index + 1}/{len(SECTIONS)}] {pdf_path.name}  ({size_kb:,.0f} KB)")

        cls = "section-start" if index >= 0 else ""
        combined_parts.append(f'<section class="{cls}">\n{body}\n</section>')

    combined_html = work_dir / "_combined.html"
    combined_html.write_text(
        page_shell(TITLE, "\n".join(combined_parts), COVER_CSS), encoding="utf-8"
    )
    combined_pdf = OUT_DIR / "AP-Invoice-Automation-Blueprint.pdf"
    print_to_pdf(browser, combined_html, combined_pdf)
    print(
        f"\nCombined: {combined_pdf}  "
        f"({combined_pdf.stat().st_size / 1024:,.0f} KB)"
    )

    if not args.keep_html:
        shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
