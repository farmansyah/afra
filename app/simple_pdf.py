"""A small PDF writer for plain documents (title, headings, paragraphs, bullets, tables as lines), so an essay can be
exported as PDF without a PDF library. Text is set in the built-in Helvetica fonts (Windows Western European
encoding): letters outside it are replaced by "?". For a fully formatted file use the Word export.
"""
import re
import zlib

A4 = (595.28, 841.89)
MARGIN = 64
# Helvetica advance widths (1/1000 em) for ASCII 32..126; bold is set about 5% wider
W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
     278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667,
     611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,
     556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def width(text: str, size: float, bold: bool = False) -> float:
    w = sum(W[ord(c) - 32] if 32 <= ord(c) < 127 else 556 for c in text)
    return w * size / 1000 * (1.05 if bold else 1)


def plain(s: str) -> str:
    """Markdown inline marks removed: **bold**, *italic*, `code`, [text](url), HTML tags."""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    return s.replace("`", "").strip()


def wrap(text: str, size: float, max_w: float, bold: bool = False) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if cur and width(trial, size, bold) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines or [""]


def blocks(markdown: str):
    """(kind, text): h1 h2 h3 p li row"""
    para = []
    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m or not line.strip() or re.match(r"^\s*([-*+]|\d+[.)])\s+", line) or line.lstrip().startswith("|"):
            if para:
                yield "p", plain(" ".join(para))
                para = []
        if m:
            yield f"h{min(3, len(m.group(1)))}", plain(m.group(2))
        elif not line.strip():
            continue
        elif re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            yield "li", plain(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line))
        elif line.lstrip().startswith("|"):
            if not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line):
                yield "row", plain("   ".join(c.strip() for c in line.strip().strip("|").split("|")))
        else:
            para.append(line.strip())
    if para:
        yield "p", plain(" ".join(para))


def pdf_text(s: str) -> bytes:
    b = s.encode("cp1252", "replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build(title: str, markdown: str, refs: list[str] | None = None) -> bytes:
    """A4 PDF: the title, the markdown body, then a References list."""
    pages, cur = [], []
    state = {"y": A4[1] - MARGIN}
    body_w = A4[0] - 2 * MARGIN

    def new_page():
        nonlocal cur
        if cur:
            pages.append(cur)
        cur = []
        state["y"] = A4[1] - MARGIN

    def put(lines, size, bold=False, indent=0.0, before=0.0, after=0.0, lead=1.35, bullet=False):
        state["y"] -= before
        for i, ln in enumerate(lines):
            if state["y"] - size * lead < MARGIN:
                new_page()
            state["y"] -= size * lead
            x = MARGIN + indent
            if bullet and i == 0:
                cur.append((MARGIN + indent - 12, state["y"], size, False, "•"))
            cur.append((x, state["y"], size, bold, ln))
        state["y"] -= after

    put(wrap(plain(title or "Essay"), 22, body_w, True), 22, True, after=10, lead=1.25)
    for kind, text in blocks(markdown):
        if kind == "h1":
            put(wrap(text, 17, body_w, True), 17, True, before=12, after=4, lead=1.25)
        elif kind == "h2":
            put(wrap(text, 14, body_w, True), 14, True, before=10, after=3, lead=1.25)
        elif kind == "h3":
            put(wrap(text, 12, body_w, True), 12, True, before=8, after=2, lead=1.25)
        elif kind == "li":
            put(wrap(text, 11, body_w - 18), 11, indent=18, after=3, bullet=True)
        elif kind == "row":
            put(wrap(text, 10, body_w), 10, after=1)
        else:
            put(wrap(text, 11, body_w), 11, after=7)
    if refs:
        put(["References"], 14, True, before=14, after=4, lead=1.25)
        for r in refs:
            put(wrap(plain(r), 10, body_w - 18), 10, indent=18, after=4, lead=1.3)
    if cur or not pages:
        pages.append(cur)

    objs: list[bytes] = [b"", b"", b"", b"", b""]  # 1 catalog, 2 pages, 3 regular font, 4 bold font, 5 reserved
    kids = []
    for items in pages:
        stream = b"BT\n" + b"".join(
            f"/F{2 if bold else 1} {size:g} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm (".encode() + pdf_text(text) + b") Tj\n" for x, y, size, bold, text in items) + b"ET"
        data = zlib.compress(stream)
        objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        cid = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {A4[0]} {A4[1]}] /Contents {cid} 0 R "
                    f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>".encode())
        kids.append(len(objs))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    objs[2] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    objs[4] = b"<< >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
