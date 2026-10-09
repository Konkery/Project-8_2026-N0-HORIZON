import argparse
import html
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_MD = PROJECT_ROOT / "04__Договор" / "pre-contract-negotiations.md"
BOOKMARKS = SCRIPT_DIR / "pdf_add_bookmarks.py"
MERMAID_CANDIDATES = [
    Path(os.path.expandvars(r"%USERPROFILE%"))
    / ".vscode"
    / "extensions"
    / "shd101wyy.markdown-preview-enhanced-0.8.39"
    / "crossnote"
    / "dependencies"
    / "mermaid"
    / "mermaid.min.js"
]

CSS = """
@page { size: A4; }
html { -webkit-print-color-adjust: exact; }
body {
  font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.5; color: #1f2328; margin: 0;
}
h1 { font-size: 17pt; border-bottom: 2px solid #d1d5da; padding-bottom: 6px; margin: 22px 0 10px; }
h2 { font-size: 14pt; border-bottom: 1px solid #d1d5da; padding-bottom: 5px; margin: 20px 0 8px; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 16px 0 6px; page-break-after: avoid; }
h4 { font-size: 10.5pt; margin: 12px 0 6px; page-break-after: avoid; }
p { margin: 6px 0; }
a { color: #0969da; text-decoration: none; }
table { border-collapse: collapse; margin: 10px 0; font-size: 9.5pt; }
th, td { border: 1px solid #9aa2ac; padding: 4px 8px; }
th { background: #eef1f4; }
tr { page-break-inside: avoid; }
code, pre {
  font-family: Consolas, 'Cascadia Mono', monospace; font-size: 9pt;
  background: #f3f4f6; border-radius: 4px;
}
code { padding: 1px 4px; }
pre { padding: 8px 10px; overflow-x: auto; page-break-inside: avoid; }
blockquote {
  margin: 8px 0; padding: 6px 12px; color: #4d545c;
  border-left: 4px solid #9aa2ac; background: #f7f8fa;
}
hr { border: 0; border-top: 2px solid #d1d5da; margin: 16px 0; }
ul, ol { margin: 6px 0 6px 20px; padding: 0; }
li { margin: 3px 0; }
.mermaid { margin: 14px 0; text-align: center; page-break-inside: avoid; }
.pagebreak { page-break-after: always; }
strong { font-weight: 600; }
"""

INIT_JS = """
window.__render_ok = false;
window.addEventListener('DOMContentLoaded', async () => {
  try {
    mermaid.initialize({
      startOnLoad: false,
      flowchart: { wrappingWidth: 300, nodeSpacing: 30, rankSpacing: 34, padding: 12, useMaxWidth: true },
    });
    if (typeof mermaid.run === 'function') {
      await mermaid.run({ querySelector: '.mermaid' });
    } else {
      mermaid.init(undefined, document.querySelectorAll('.mermaid'));
    }
    const avail = 960;
    document.querySelectorAll('.mermaid svg').forEach((svg) => {
      const h = svg.getBoundingClientRect().height;
      if (h > avail) svg.style.zoom = avail / h;
    });
    const first = document.querySelectorAll('.mermaid')[2];
    const rects = first ? Array.from(first.querySelectorAll('rect')).map((r) => Math.round(r.getBoundingClientRect().width)) : [];
    window.__diag = {
      nodeWidths: rects,
      svgs: Array.from(document.querySelectorAll('.mermaid svg')).map(
        (s) => [Math.round(s.getBoundingClientRect().width), Math.round(s.getBoundingClientRect().height)]),
    };
  } catch (e) {
    window.__mermaid_error = (e && (e.str || e.message))
      ? String(e.str || e.message)
      : (() => { try { return JSON.stringify(e); } catch (_) { return String(e); } })();
  } finally {
    window.__render_ok = true;
  }
});
"""


def github_slug(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).lower()
    out = []
    for ch in s:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    return "".join(out)


def check_tables(md_text: str, body_html: str):
    problems = []
    lines = md_text.splitlines()
    fence = None
    blocks = []
    start = None
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        m = re.match(r"^(`{3,}|~{3,})", stripped)
        if m:
            marker = m.group(1)[0]
            if fence is None:
                fence = marker
            elif stripped.startswith(marker * 3):
                fence = None
            continue
        if fence:
            continue
        if stripped.startswith("|"):
            if start is None:
                start = i
        elif start is not None:
            blocks.append((start, i - 1))
            start = None
    if start is not None:
        blocks.append((start, len(lines)))

    def cells(row: str) -> int:
        r = row.strip()
        if r.startswith("|"):
            r = r[1:]
        if r.endswith("|"):
            r = r[:-1]
        return len(r.split("|"))

    for s, e in blocks:
        rows = lines[s - 1:e]
        if len(rows) < 2:
            problems.append(f"стр {s}: табличный блок из одной строки")
            continue
        if "-" not in rows[1] or not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", rows[1]):
            problems.append(f"стр {s + 1}: отсутствует строка-разделитель таблицы")
            continue
        n = cells(rows[0])
        for k, row in enumerate(rows):
            if cells(row) != n:
                problems.append(f"стр {s + k}: ячеек {cells(row)}, ожидается {n} (как в шапке)")
    n_html = body_html.count("<table>")
    if len(blocks) != n_html:
        problems.append(f"блоков таблиц в md: {len(blocks)}, распознано парсером: {n_html}")
    return problems


def md_to_html_body(md_text: str) -> str:
    import markdown_it

    renderer = markdown_it.MarkdownIt("commonmark", {"html": True, "breaks": False}).enable("table")
    body = renderer.render(md_text)
    body = body.replace("<!-- pagebreak -->", '<div class="pagebreak"></div>')

    def fence_to_div(m: re.Match) -> str:
        inner = html.unescape(m.group(1))
        return f'<div class="mermaid">\n{inner}\n</div>'

    body = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        fence_to_div,
        body,
        flags=re.S,
    )

    used = {}
    def add_id(m: re.Match) -> str:
        level, inner = m.group(1), m.group(2)
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        base = github_slug(plain)
        n = used.get(base, 0)
        used[base] = n + 1
        slug = base if n == 0 else f"{base}-{n}"
        return f'<h{level} id="{slug}">{inner}</h{level}>'

    body = re.sub(r"<h([1-4])>(.*?)</h\1>", add_id, body, flags=re.S)
    return body


def build_html(body: str, mermaid_src: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
<script src="{mermaid_src}"></script>
</head>
<body>
{body}
<script>{INIT_JS}</script>
</body>
</html>"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        description="Детерминированный конвейер md -> PDF (Playwright/Edge) + закладки.",
    )
    ap.add_argument("md", nargs="?", default=str(DEFAULT_MD), help="путь к markdown-отчёту")
    ap.add_argument("pdf", nargs="?", default=None, help="куда сохранить PDF (по умолчанию — рядом с md)")
    ap.add_argument("--keep-html", action="store_true", help="не удалять промежуточный HTML")
    args = ap.parse_args()

    md_path = Path(args.md).resolve()
    pdf_path = Path(args.pdf).resolve() if args.pdf else md_path.with_suffix(".pdf")
    mermaid_file = next((p for p in MERMAID_CANDIDATES if p.is_file()), None)
    if mermaid_file is None:
        mermaid_src = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"
        print("mermaid: CDN (локальная копия не найдена)")
    else:
        mermaid_src = mermaid_file.as_uri()
        print(f"mermaid: {mermaid_file}")

    md_text = md_path.read_text(encoding="utf-8")
    body = md_to_html_body(md_text)
    problems = check_tables(md_text, body)
    if problems:
        print("ERROR: markdown-таблицы повреждены — парсер их не признаёт:")
        for p in problems:
            print("  ", p)
        print("Исправьте исходник (число ячеек разделителя = шапке, пустые строки вокруг <!-- pagebreak -->) и повторите.")
        return 4
    title_m = re.search(r"^#\s+(.+)$", md_text, flags=re.M)
    title = title_m.group(1).strip() if title_m else md_path.stem
    page = build_html(body, mermaid_src, title)
    html_path = pdf_path.with_suffix(".tmp.html")
    html_path.write_text(page, encoding="utf-8")
    print(f"html: {html_path.name} ({len(page)//1024} КБ)")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        pg = browser.new_page()
        pg.goto(html_path.as_uri(), wait_until="networkidle", timeout=60000)
        try:
            pg.wait_for_function(
                """() => window.__render_ok === true
                      && Array.from(document.querySelectorAll('.mermaid')).every(
                           el => el.querySelector('svg')
                              && el.querySelector('svg').getBoundingClientRect().height > 30)""",
                timeout=90000,
            )
            print("render: все mermaid-схемы дорендерены")
        except Exception:
            diag = pg.evaluate(
                """() => Array.from(document.querySelectorAll('.mermaid')).map(
                     el => ({ hasSvg: !!el.querySelector('svg'),
                              head: (el.querySelector('svg')
                                     ? el.querySelector('svg').getBoundingClientRect().height
                                     : -1) }))"""
            )
            err = pg.evaluate("() => window.__mermaid_error || null")
            print(f"ERROR: mermaid не дорендерилась; diag={diag}; js-error={err}")
            browser.close()
            return 2
        diag_ok = pg.evaluate("() => window.__diag || null")
        print(f"diag: {diag_ok}")
        pg.emulate_media(media="print")
        pg.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<span></span>",
            footer_template=(
                "<div style=\"width:100%;text-align:center;"
                "font-family:'Segoe UI',Arial,sans-serif;font-size:9px;color:#6b7280;\">"
                "стр. <span class=\"pageNumber\"></span> / <span class=\"totalPages\"></span></div>"
            ),
            margin={"top": "14mm", "bottom": "16mm", "left": "13mm", "right": "13mm"},
        )
        browser.close()
    print(f"pdf: {pdf_path.name}")

    rc = subprocess.run(
        [sys.executable, str(BOOKMARKS), str(md_path), str(pdf_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(rc.stdout.strip().splitlines()[-3:])
    if rc.returncode != 0:
        print(rc.stderr[-500:])
        return 3

    if not args.keep_html:
        html_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
