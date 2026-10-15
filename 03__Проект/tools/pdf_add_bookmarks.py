import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

SCRIPT_ROOT = Path(__file__).resolve().parent.parent.parent

# Постоянное хранение отчёта (перенесён и переименован 14.10.2026, журнал 4.24):
REPORT_DIR = SCRIPT_ROOT / "01__Документация" / "Протоколы" / "01__преддоговорные переговоры"
REPORT_STEM = "01__Отчет-преддоговорные переговоры - 2.10.2026"
REPORT_GLOB = "01__Отчет-преддоговорные переговоры*"   # захватывает pdf, переименованный пользователем («... от 2.10.2026.pdf»)
LEGACY_STEMS = ("pre-contract-negotiations",)           # старые канонические имена (до 14.10.2026)

def _repo_wide(pattern: str):
    matches = []
    for p in SCRIPT_ROOT.rglob(pattern):
        if ".git" in p.parts or "node_modules" in p.parts:
            continue
        matches.append(p)
    return matches


def find_default(ext: str):
    # 1) каноническая пара в постоянном хранении
    preferred = REPORT_DIR / f"{REPORT_STEM}{ext}"
    if preferred.is_file():
        return preferred
    # 2) переименованные копии того же документа в постоянном хранении (по mtime)
    if REPORT_DIR.is_dir():
        renamed = sorted(REPORT_DIR.glob(REPORT_GLOB + ext), key=lambda p: p.stat().st_mtime)
        if renamed:
            return renamed[-1]
    # 3) наследие: старое расположение 04__Договор и старые имена
    for stem in LEGACY_STEMS:
        legacy = SCRIPT_ROOT / "04__Договор" / f"{stem}{ext}"
        if legacy.is_file():
            return legacy
    matches = _repo_wide(REPORT_GLOB + ext)
    for stem in LEGACY_STEMS:
        matches += _repo_wide(f"{stem}{ext}")
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)

EPILOG = """\
Workflow (после каждого экспорта MPE закладки затираются — их нужно вшивать заново):
  1) отредактировать .md (разрывы страниц — маркер <!-- pagebreak -->)
  2) VSC / Markdown Preview Enhanced: Export -> Chrome (Puppeteer) -> PDF
     (в settings.json: "markdown-preview-enhanced.puppeteerWaitForTimeout": 5000)
  3) запустить этот скрипт (или двойной клик make-pdf.cmd)
  4) открыть PDF — боковая панель ридера = дерево заголовков

Примеры:
  python pdf_add_bookmarks.py                        авторежим: находит отчет сам
  python pdf_add_bookmarks.py файл.md файл.pdf      явные пути (оба аргумента обязательны)
  python pdf_add_bookmarks.py -o выход.pdf           авторежим + сохранить в другой файл
  python pdf_add_bookmarks.py -h                     эта справка

Правила извлечения заголовков:
  - распознаются h1-h4; заголовок с "{ignore=true}" на конце пропускается;
  - содержимое code-fence (``` и ~~~) не сканируется;
  - страница заголовка ищется нормализованным текстом последовательно сверху вниз;
    ненайденный заголовок получает страницу предыдущего + предупреждение (WARN);
  - повторные прогоны идемпотентны: дерево закладок заменяется целиком, не дублируется.
"""


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    for ch in "«»\"'“”‘’()[]{}·•":
        s = s.replace(ch, " ")
    s = re.sub(r"[^a-zа-я0-9 ]+", " ", s)
    return " ".join(s.split())


def find_default(ext: str):
    preferred = SCRIPT_ROOT / "04__Договор" / f"{REPORT_STEM}{ext}"
    if preferred.is_file():
        return preferred
    matches = []
    for p in SCRIPT_ROOT.rglob(f"{REPORT_STEM}{ext}"):
        if ".git" in p.parts or "node_modules" in p.parts:
            continue
        matches.append(p)
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def parse_headings(md_path):
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
            headings.append((len(m.group(1)), raw))
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
    writer.root_object[NameObject("/PageMode")] = NameObject("/UseOutlines")
    out_path = Path(out_path)
    if out_path.resolve() == Path(pdf_path).resolve():
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=str(out_path.parent))
        os.close(fd)
        with open(tmp, "wb") as fh:
            writer.write(fh)
        os.replace(tmp, out_path)
    else:
        with open(out_path, "wb") as fh:
            writer.write(fh)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="pdf_add_bookmarks.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Вшивает в PDF дерево закладок (структуру документа) по заголовкам markdown-источника. "
                    "Без аргументов — сам находит файл отчета в постоянном хранении "
                    "(01__Документация\\Протоколы\\01__преддоговорные переговоры).",
        epilog=EPILOG,
    )
    ap.add_argument(
        "paths",
        nargs="*",
        metavar="MD|PDF",
        help="пути к markdown-источнику и PDF; без аргументов — автоматический поиск отчета",
    )
    ap.add_argument("-o", "--out", default=None, help="куда сохранить (по умолчанию — перезапись PDF на месте)")
    args = ap.parse_args()

    if len(args.paths) == 0:
        md, pdf = find_default(".md"), find_default(".pdf")
        if md is None or pdf is None:
            ap.error("автопоиск не нашел пару файлов отчета (нужны .md и .pdf с именем "
                     f"'{REPORT_STEM}') — укажите пути явно")
        print(f"AUTO md : {md}")
        print(f"AUTO pdf: {pdf}")
    elif len(args.paths) == 2:
        md, pdf = map(Path, args.paths)
    else:
        ap.error("при явном указании путей нужны ОБА аргумента: сначала MD, затем PDF "
                 "(или ни одного — тогда включается автопоиск)")

    if not md.is_file():
        ap.error(f"markdown не найден: {md}")
    if not pdf.is_file():
        ap.error(f"PDF не найден: {pdf}")

    headings = parse_headings(md)
    if not headings:
        print("ERROR: заголовки в md не найдены")
        return 1
    print(f"--- карта закладок ({len(headings)} заголовков) ---")
    reader = PdfReader(pdf)
    pages_norm = [norm(p.extract_text() or "") for p in reader.pages]
    mapping = locate_pages(pages_norm, headings)
    build_output(pdf, mapping, args.out or pdf)

    verify = PdfReader(args.out or pdf)

    def total(items):
        return sum(total(i) if isinstance(i, list) else 1 for i in items)

    n = total(verify.outline or [])
    print(f"HEADINGS={len(headings)} OUTLINE_ITEMS={n}")
    if n != len(headings):
        print("WARN: количество закладок не совпало с числом заголовков")
    return 0


if __name__ == "__main__":
    sys.exit(main())
