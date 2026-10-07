import argparse
import os
import re
import sys
import tempfile

from pypdf import PdfReader, PdfWriter


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    for ch in "«»\"'“”‘’()[]{}·•":
        s = s.replace(ch, " ")
    s = re.sub(r"[^a-zа-я0-9 ]+", " ", s)
    return " ".join(s.split())


def parse_headings(md_path: str):
    headings = []
    fence = None
    with open(md_path, encoding="utf-8") as fh:
        for line in fh:
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
            m = re.match(r"^(#{1,4})\s+(.*\S)\s*$", line)
            if not m:
                continue
            raw = m.group(2).strip()
            if re.search(r"\{\s*ignore\s*=\s*true\s*\}\s*$", raw):
                continue
            level = len(m.group(1))
            title = raw
            headings.append((level, title))
    return headings


def locate_pages(pages_norm, headings):
    mapping = []
    start = 0
    prev_page = 0
    for level, title in headings:
        key = norm(re.sub(r"[`*_]", "", title))
        found = None
        for size in (60, 30, 15):
            probe = key[:size]
            if not probe:
                break
            for pi in range(start, len(pages_norm)):
                if probe in pages_norm[pi]:
                    found = pi
                    break
            if found is not None:
                break
        if found is None:
            print(f"WARN  p{prev_page + 1:<3} (не найден) {title}")
            found = prev_page
        else:
            start = found
        print(f"ok    p{found + 1:<3} {'  ' * (level - 1)}{title}")
        mapping.append((level, title, found))
        prev_page = found
    return mapping


def build_output(pdf_path, mapping, out_path):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for pg in reader.pages:
        writer.add_page(pg)
    stack = {}
    for level, title, page in mapping:
        clean = re.sub(r"[`*_]", "", title).strip()
        parent = None
        for shallower in range(level - 1, 0, -1):
            if shallower in stack:
                parent = stack[shallower]
                break
        ref = writer.add_outline_item(clean, page, parent=parent)
        stack[level] = ref
        for deeper in [k for k in stack if k > level]:
            del stack[deeper]
    if os.path.abspath(out_path) == os.path.abspath(pdf_path):
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(os.path.abspath(pdf_path)))
        os.close(fd)
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, pdf_path)
    else:
        with open(out_path, "wb") as fh:
            writer.write(fh)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Вшивает дерево закладок из md-заголовков в PDF-экспорт")
    ap.add_argument("md", help="путь к markdown-источнику")
    ap.add_argument("pdf", help="путь к PDF (перезаписывается, если --out не задан)")
    ap.add_argument("-o", "--out", default=None, help="альтернативный путь сохранения")
    args = ap.parse_args()

    headings = parse_headings(args.md)
    if not headings:
        print("ERROR: заголовки в md не найдены")
        return 1
    reader = PdfReader(args.pdf)
    pages_norm = [norm(p.extract_text() or "") for p in reader.pages]
    mapping = locate_pages(pages_norm, headings)
    build_output(args.pdf, mapping, args.out or args.pdf)
    verify = PdfReader(args.out or args.pdf)

    def total(items):
        return sum(total(i) if isinstance(i, list) else 1 for i in items)

    n = total(verify.outline or [])
    print(f"HEADINGS={len(headings)} OUTLINE_ITEMS={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
