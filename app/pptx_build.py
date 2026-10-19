"""The .pptx engine: the same slide content as the Google engine, written as an editable PowerPoint file.

Plain zip + XML (no python-pptx). The deck uses the design's palette and fonts on A.F.R.A's own layouts (title,
section, content, two columns, figure, table, chart, references, closing), 16:9 at 720 x 405 pt, with speaker notes.
Without a Google connection the file goes to "My Drive/A.F.R.A Decks" when Google Drive for desktop is installed:
Drive uploads it, and Google Slides opens it (File › Save as Google Slides converts it).
"""
import os
import re
import struct
import time
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

from . import designs

EMU = 12700
W, H = 720, 405
FOLDER_NAME = "A.F.R.A Decks"  # same name as the Google Drive folder the Slides engine uses
NSDECL = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
          'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument.presentationml"
BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def x(s) -> str:
    return escape(BAD.sub("", str(s or "")), {'"': "&quot;"})


def e(pt: float) -> int:
    return int(round(pt * EMU))


def c(hexs: str) -> str:
    return (hexs or "#000000").lstrip("#").upper()[:6]


# ---------------------------------------------------------------- shapes
class Slide:
    def __init__(self, bg: str):
        self.bg, self.parts, self.n, self.images = bg, [], 2, []

    def _id(self) -> int:
        self.n += 1
        return self.n

    def rect(self, x0, y0, w, h, fill, line="", prst="rect", solid_line=False):
        i = self._id()
        dash = "" if solid_line else '<a:prstDash val="dash"/>'
        ln = f'<a:ln w="9525"><a:solidFill><a:srgbClr val="{c(line)}"/></a:solidFill>{dash}</a:ln>' if line else "<a:ln><a:noFill/></a:ln>"
        fill_xml = f'<a:solidFill><a:srgbClr val="{c(fill)}"/></a:solidFill>' if fill else "<a:noFill/>"
        self.parts.append(f'<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="Shape {i}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>'
                          f'<a:xfrm><a:off x="{e(x0)}" y="{e(y0)}"/><a:ext cx="{e(w)}" cy="{e(h)}"/></a:xfrm><a:prstGeom prst="{x(prst)}"><a:avLst/></a:prstGeom>'
                          f'{fill_xml}{ln}</p:spPr></p:sp>')

    def text(self, x0, y0, w, h, paras, anchor="t", name="Text"):
        """paras: [(text, {size, bold, color, font, bullet, align, space})]"""
        i = self._id()
        body = "".join(_para(t, st) for t, st in paras) or "<a:p><a:endParaRPr lang=\"en-US\"/></a:p>"
        self.parts.append(f'<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="{x(name)} {i}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>'
                          f'<a:xfrm><a:off x="{e(x0)}" y="{e(y0)}"/><a:ext cx="{e(w)}" cy="{e(h)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                          f'<a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720" anchor="{anchor}">'
                          f'<a:normAutofit/></a:bodyPr><a:lstStyle/>{body}</p:txBody></p:sp>')

    def table(self, x0, y0, w, h, header, rows, t):
        i = self._id()
        data = ([list(header)] if header else []) + [list(r) for r in rows]
        ncols = max(len(r) for r in data)
        size = 12 if len(data) <= 6 else 11 if len(data) <= 9 else 10
        colw = e(w) // ncols
        rowh = min(e(h) // max(1, len(data)), e(34))
        trs = []
        for ri, row in enumerate(data):
            head = bool(header) and ri == 0
            tcs = []
            for ci in range(ncols):
                val = row[ci] if ci < len(row) else ""
                st = {"size": size, "bold": head, "color": t["light"] if head else t["text"], "font": t["body_font"]}
                fill = c(t["dark"]) if head else ("F4F6F9" if ri % 2 else "FFFFFF")
                tcs.append(f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{_para(val, st)}</a:txBody>'
                           f'<a:tcPr marL="68580" marR="68580" marT="34290" marB="34290"><a:solidFill><a:srgbClr val="{fill}"/></a:solidFill></a:tcPr></a:tc>')
            trs.append(f'<a:tr h="{rowh}">{"".join(tcs)}</a:tr>')
        grid = "".join(f'<a:gridCol w="{colw}"/>' for _ in range(ncols))
        self.parts.append(f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{i}" name="Table {i}"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/>'
                          f'</p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="{e(x0)}" y="{e(y0)}"/><a:ext cx="{colw * ncols}" cy="{rowh * len(data)}"/></p:xfrm>'
                          f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="{int(bool(header))}" bandRow="1"/>'
                          f'<a:tblGrid>{grid}</a:tblGrid>{"".join(trs)}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')

    def picture(self, x0, y0, w, h, data: bytes, ext: str):
        i = self._id()
        iw, ih = image_size(data) or (w, h)
        k = min(w / iw, h / ih)
        pw, ph = iw * k, ih * k
        rid = f"rId{10 + len(self.images)}"
        self.images.append((rid, data, ext))
        self.parts.append(f'<p:pic><p:nvPicPr><p:cNvPr id="{i}" name="Picture {i}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
                          f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>'
                          f'<a:xfrm><a:off x="{e(x0 + (w - pw) / 2)}" y="{e(y0 + (h - ph) / 2)}"/><a:ext cx="{e(pw)}" cy="{e(ph)}"/></a:xfrm>'
                          f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')

    def xml(self) -> str:
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld {NSDECL}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="{c(self.bg)}"/>'
                f'</a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                f'<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                f'{"".join(self.parts)}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def _para(text, st: dict) -> str:
    ppr = []
    if st.get("bullet"):
        ppr.append(f'<a:pPr marL="285750" indent="-285750"><a:spcBef><a:spcPts val="{int(st.get("space", 6) * 100)}"/></a:spcBef>'
                   f'<a:buClr><a:srgbClr val="{c(st.get("bullet_color") or st.get("color"))}"/></a:buClr><a:buFont typeface="Arial"/><a:buChar char="•"/></a:pPr>')
    elif st.get("align") or st.get("space"):
        al = f' algn="{st["align"]}"' if st.get("align") else ""
        sp = f'<a:spcBef><a:spcPts val="{int(st["space"] * 100)}"/></a:spcBef>' if st.get("space") else ""
        ppr.append(f'<a:pPr{al}>{sp}<a:buNone/></a:pPr>')
    else:
        ppr.append("<a:pPr><a:buNone/></a:pPr>")
    rpr = (f'<a:rPr lang="en-US" sz="{int(st.get("size", 18) * 100)}" b="{int(bool(st.get("bold")))}" dirty="0">'
           f'<a:solidFill><a:srgbClr val="{c(st.get("color", "#000000"))}"/></a:solidFill>'
           f'<a:latin typeface="{x(st.get("font", "Arial"))}"/><a:cs typeface="{x(st.get("font", "Arial"))}"/></a:rPr>')
    run = f'<a:r>{rpr}<a:t>{x(text)}</a:t></a:r>' if str(text or "") else ""
    return f'<a:p>{"".join(ppr)}{run}<a:endParaRPr lang="en-US" sz="{int(st.get("size", 18) * 100)}" dirty="0"/></a:p>'


def image_size(data: bytes) -> tuple[int, int] | None:
    """Width and height of a PNG or JPEG, read from its header."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except struct.error:
        pass
    return None


# ---------------------------------------------------------------- one slide per layout
def render(s: dict, t: dict, number: int, images: dict) -> tuple[Slide, list[str]]:
    from .gslides import slide_texts  # same text for both engines
    L = s.get("layout", "content")
    dark = L in ("title", "section", "closing")
    sl = Slide(t["dark"] if dark else t["light"])
    tx = slide_texts(s)
    notes = []
    head = {"font": t["heading_font"], "bold": True, "color": t["light"] if dark else t["text"]}
    body = {"font": t["body_font"], "color": t["text"], "size": 18}
    if dark:
        sl.rect(48, 112 if L != "section" else 142, 72, 5, t["accent"])
        big = 40 if L in ("title", "closing") else 34
        sl.text(48, 120 if L != "section" else 150, 624, 100 if L != "section" else 80, [(tx["title"], {**head, "size": big})], anchor="b", name="Title")
        if tx.get("subtitle"):
            sl.text(48, 232 if L != "section" else 240, 624, 70, [(tx["subtitle"], {"font": t["body_font"], "color": t["accent"], "size": 18})], name="Subtitle")
        return sl, notes
    sl.rect(40, 80, 56, 4, t["accent"])
    sl.text(40, 20, 640, 56, [(tx["title"], {**head, "size": 26})], anchor="b", name="Title")
    sl.text(640, 380, 64, 20, [(str(number), {"font": t["body_font"], "color": t["muted"], "size": 10, "align": "r"})], name="Slide number")
    bullets = lambda items, size=18: [(b, {**body, "size": size, "bullet": True, "bullet_color": t["accent2"], "space": 6 if size > 12 else 2}) for b in items]
    if L in ("content", "references"):
        size = 11 if L == "references" else 18
        sl.text(40, 96 if L == "content" else 90, 640, 284 if L == "content" else 296, bullets(s.get("bullets") or [], size), name="Body")
    elif L == "two_column":
        for x0, col in ((40, s.get("left") or {}), (370, s.get("right") or {})):
            paras = ([(col["heading"], {**body, "bold": True, "color": t["accent2"], "size": 18})] if col.get("heading") else []) + bullets(col.get("bullets") or [], 16)
            sl.text(x0, 96, 310, 284, paras, name="Column")
    elif L in ("figure", "chart", "table"):
        cap = tx.get("caption", "")
        if L == "table":
            area, cap_box = (40, 96, 640, 250), (40, 352, 640, 30)
        else:
            area, cap_box = (40, 96, 420, 276), (476, 96, 204, 276)
        tb = s.get("table") or {}
        img = images.get((s.get("figure") or {}).get("url", ""))
        if L == "table" and tb.get("rows"):
            sl.table(*area, tb.get("header") or [], tb["rows"], t)
        elif L == "figure" and img:
            sl.picture(*area, *img)
        else:
            what = {"chart": "Chart: insert it from the Google Sheet (Insert › Chart › From Sheets)", "table": "Table"}.get(L, "Figure")
            sl.rect(*area, "#F1F3F6", "#B8BFCC")
            sl.text(*area, [(what, {"font": t["body_font"], "color": t["muted"], "size": 14, "align": "ctr"})], anchor="ctr", name="Placeholder")
            notes.append(f"no {L} to insert in the PowerPoint: add it by hand")
        if cap:
            sl.text(*cap_box, [(cap, {"font": t["body_font"], "color": t["muted"], "size": 13})], name="Caption")
    return sl, notes


# ---------------------------------------------------------------- the package
def _theme_xml(t: dict) -> str:
    cols = t.get("colors") or {}

    def clr(slot, val):
        return f'<a:{slot}><a:srgbClr val="{c(cols.get(slot) or val)}"/></a:{slot}>'
    scheme = "".join([clr("dk1", t["text"]), clr("lt1", t["light"]), clr("dk2", t["dark"]), clr("lt2", "#E7E6E6"), clr("accent1", t["accent"]),
                      clr("accent2", t["accent2"]), clr("accent3", "#A5A5A5"), clr("accent4", "#5B9BD5"), clr("accent5", "#70AD47"),
                      clr("accent6", "#ED7D31"), clr("hlink", t["accent2"]), clr("folHlink", "#954F72")])
    font = lambda f: f'<a:latin typeface="{x(f)}"/><a:ea typeface=""/><a:cs typeface=""/>'
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    ln = '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="{x(t.get("name", "A.F.R.A"))}">'
            f'<a:themeElements><a:clrScheme name="{x(t.get("name", "A.F.R.A"))}">{scheme}</a:clrScheme>'
            f'<a:fontScheme name="A.F.R.A"><a:majorFont>{font(t["heading_font"])}</a:majorFont><a:minorFont>{font(t["body_font"])}</a:minorFont></a:fontScheme>'
            f'<a:fmtScheme name="A.F.R.A"><a:fillStyleLst>{fill * 3}</a:fillStyleLst><a:lnStyleLst>{ln * 3}</a:lnStyleLst>'
            f'<a:effectStyleLst>{"<a:effectStyle><a:effectLst/></a:effectStyle>" * 3}</a:effectStyleLst><a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst>'
            f'</a:fmtScheme></a:themeElements></a:theme>')


TREE = ('<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{}</p:spTree>')
CLRMAP = '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
XMLH = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _ph(i, name, kind, idx, x0, y0, w, h, text=""):
    body = f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" dirty="0"/><a:t>{x(text)}</a:t></a:r></a:p></p:txBody>' if text is not None else ""
    lock = '<a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/>' if kind == "sldImg" else '<a:spLocks noGrp="1"/>'
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="{name}"/><p:cNvSpPr>{lock}</p:cNvSpPr><p:nvPr><p:ph type="{kind}"{f" idx={chr(34)}{idx}{chr(34)}" if idx else ""}/></p:nvPr></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x0}" y="{y0}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>{body}</p:sp>')


def _rels(items) -> str:
    return XMLH + f'<Relationships xmlns="{RELS_NS}">' + "".join(
        f'<Relationship Id="{i}" Type="{R}/{t}" Target="{x(tg)}"/>' if not t.startswith("http") else f'<Relationship Id="{i}" Type="{t}" Target="{x(tg)}"/>'
        for i, t, tg in items) + "</Relationships>"


def build_pptx(slides: list[dict], t: dict, title: str = "", images: dict | None = None, size: tuple[float, float] | None = None,
               renderer=None, thumbnail: tuple[bytes, str] | None = None) -> tuple[bytes, list[dict]]:
    """slides (content JSON after fit_slides) -> (.pptx bytes, flags). images: url -> (bytes, "png"|"jpeg").
    size (points) and renderer(slide, theme, number, images) -> (Slide, notes) let template_import write rebuilt designs."""
    import io
    import zipfile
    t = {**designs.THEME, **(t or {})}
    renderer = renderer or render
    sw, sh = size or (W, H)
    files, flags = {}, []
    exts = set()
    sld_ids, pres_rels = [], [("rId1", "slideMaster", "slideMasters/slideMaster1.xml"), ("rId2", "theme", "theme/theme1.xml"),
                              ("rId3", "notesMaster", "notesMasters/notesMaster1.xml"), ("rId4", "presProps", "presProps.xml"),
                              ("rId5", "viewProps", "viewProps.xml"), ("rId6", "tableStyles", "tableStyles.xml")]
    for n, s in enumerate(slides, 1):
        sl, notes = renderer(s, t, n, images or {})
        flags += [{"slide": n, "issue": m} for m in notes]
        files[f"ppt/slides/slide{n}.xml"] = sl.xml()
        rels = [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "notesSlide", f"../notesSlides/notesSlide{n}.xml")]
        for rid, data, ext in sl.images:
            ext = "jpeg" if ext in ("jpg", "jpeg") else "png"
            exts.add(ext)
            name = f"image{n}_{rid}.{ext}"
            files[f"ppt/media/{name}"] = data
            rels.append((rid, "image", f"../media/{name}"))
        files[f"ppt/slides/_rels/slide{n}.xml.rels"] = _rels(rels)
        note = (s.get("notes") or "").strip()
        paras = "".join(f'<a:p><a:r><a:rPr lang="en-US" dirty="0"/><a:t>{x(p)}</a:t></a:r></a:p>' for p in note.split("\n")) or "<a:p/>"
        files[f"ppt/notesSlides/notesSlide{n}.xml"] = (
            XMLH + f'<p:notes {NSDECL}><p:cSld>' + TREE.format(_ph(2, "Slide Image", "sldImg", 0, 685800, 1143000, 5486400, 3086100, None)
            + f'<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
            f'<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody></p:sp>') + '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')
        files[f"ppt/notesSlides/_rels/notesSlide{n}.xml.rels"] = _rels([("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"),
                                                                        ("rId2", "slide", f"../slides/slide{n}.xml")])
        sld_ids.append(f'<p:sldId id="{255 + n}" r:id="rId{100 + n}"/>')
        pres_rels.append((f"rId{100 + n}", "slide", f"slides/slide{n}.xml"))
    files["ppt/presentation.xml"] = (XMLH + f'<p:presentation {NSDECL} saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
                                     f'<p:notesMasterIdLst><p:notesMasterId r:id="rId3"/></p:notesMasterIdLst><p:sldIdLst>{"".join(sld_ids)}</p:sldIdLst>'
                                     f'<p:sldSz cx="{e(sw)}" cy="{e(sh)}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
    files["ppt/_rels/presentation.xml.rels"] = _rels(pres_rels)
    files["ppt/slideMasters/slideMaster1.xml"] = (XMLH + f'<p:sldMaster {NSDECL}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
                                                  + TREE.format("") + f'</p:cSld>{CLRMAP}<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>')
    files["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")])
    files["ppt/slideLayouts/slideLayout1.xml"] = (XMLH + f'<p:sldLayout {NSDECL} type="blank" preserve="1"><p:cSld name="Blank">' + TREE.format("")
                                                  + '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    files["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")])
    files["ppt/notesMasters/notesMaster1.xml"] = (XMLH + f'<p:notesMaster {NSDECL}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
                                                  + TREE.format(_ph(2, "Slide Image", "sldImg", 0, 685800, 1143000, 5486400, 3086100, None)
                                                                + _ph(3, "Notes", "body", 1, 685800, 4400550, 5486400, 3600450, ""))
                                                  + f'</p:cSld>{CLRMAP}</p:notesMaster>')
    files["ppt/notesMasters/_rels/notesMaster1.xml.rels"] = _rels([("rId1", "theme", "../theme/theme2.xml")])
    files["ppt/theme/theme1.xml"] = files["ppt/theme/theme2.xml"] = _theme_xml(t)
    files["ppt/presProps.xml"] = XMLH + f'<p:presentationPr {NSDECL}/>'
    files["ppt/viewProps.xml"] = XMLH + f'<p:viewPr {NSDECL}/>'
    files["ppt/tableStyles.xml"] = XMLH + '<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>'
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    files["docProps/core.xml"] = (XMLH + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                                  'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                                  'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                                  f'<dc:title>{x(title)}</dc:title><dc:creator>A.F.R.A</dc:creator>'
                                  f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    files["docProps/app.xml"] = (XMLH + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                                 f'<Application>A.F.R.A</Application><Slides>{len(slides)}</Slides></Properties>')
    root_rels = [("rId1", "officeDocument", "ppt/presentation.xml"),
                 ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
                 ("rId3", "extended-properties", "docProps/app.xml")]
    if thumbnail:  # the picture the Designs folder shows for this file
        ext = "jpeg" if thumbnail[1] in ("jpg", "jpeg") else "png"
        exts.add(ext)
        files[f"docProps/thumbnail.{ext}"] = thumbnail[0]
        root_rels.append(("rId4", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail", f"docProps/thumbnail.{ext}"))
    files["_rels/.rels"] = _rels(root_rels)
    over = [("/ppt/presentation.xml", f"{CT}.presentation.main+xml"), ("/ppt/slideMasters/slideMaster1.xml", f"{CT}.slideMaster+xml"),
            ("/ppt/slideLayouts/slideLayout1.xml", f"{CT}.slideLayout+xml"), ("/ppt/notesMasters/notesMaster1.xml", f"{CT}.notesMaster+xml"),
            ("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
            ("/ppt/theme/theme2.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
            ("/ppt/presProps.xml", f"{CT}.presProps+xml"), ("/ppt/viewProps.xml", f"{CT}.viewProps+xml"),
            ("/ppt/tableStyles.xml", f"{CT}.tableStyles+xml"),
            ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
            ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
    for n in range(1, len(slides) + 1):
        over += [(f"/ppt/slides/slide{n}.xml", f"{CT}.slide+xml"), (f"/ppt/notesSlides/notesSlide{n}.xml", f"{CT}.notesSlide+xml")]
    files["[Content_Types].xml"] = (XMLH + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                    '<Default Extension="xml" ContentType="application/xml"/>'
                                    + "".join(f'<Default Extension="{ext}" ContentType="image/{ext}"/>' for ext in sorted(exts))
                                    + "".join(f'<Override PartName="{p}" ContentType="{ct}"/>' for p, ct in over) + "</Types>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", files.pop("[Content_Types].xml"))
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue(), flags


# ---------------------------------------------------------------- slides from a layout spec (template_import)
ANCHOR = {"top": "t", "middle": "ctr", "bottom": "b"}
ALIGN = {"left": "l", "center": "ctr", "right": "r"}
PRST = {"rect": "rect", "ellipse": "ellipse", "round_rect": "roundRect", "triangle": "triangle", "line": "rect"}


def render_spec(s: dict, t: dict, number: int, images: dict) -> tuple[Slide, list[str]]:
    """One slide of a validated layout spec (see template_import.validate_spec): shapes, then real text boxes.
    Positions in the spec are percentages of the slide, so any slide size works."""
    sw, sh = s["_size"]
    sl = Slide(s.get("background") or t["light"])
    for sh_ in s.get("shapes") or []:
        w, h = sh_["w"] * sw / 100, sh_["h"] * sh / 100
        if sh_["type"] == "line":
            w, h = (max(w, 0.75), max(h, 0.75))
        sl.rect(sh_["x"] * sw / 100, sh_["y"] * sh / 100, w, h, sh_.get("fill"), sh_.get("line") or "", PRST.get(sh_["type"], "rect"), solid_line=True)
    for tx in s.get("texts") or []:
        font = tx.get("font") or (t["heading_font"] if tx.get("role") in ("title", "subtitle") else t["body_font"])
        style = {"size": tx["size"], "bold": tx.get("bold"), "color": tx["color"], "font": font, "align": ALIGN.get(tx.get("align"), "l")}
        lines = [ln for ln in str(tx["text"]).split("\n")] or [""]
        sl.text(tx["x"] * sw / 100, tx["y"] * sh / 100, tx["w"] * sw / 100, tx["h"] * sh / 100, [(ln, style) for ln in lines],
                anchor=ANCHOR.get(tx.get("anchor"), "t"), name=(tx.get("role") or "text").title())
    return sl, []


def build_from_spec(deck: dict, title: str = "", thumbnail: tuple[bytes, str] | None = None) -> bytes:
    """A validated deck spec (size in points, theme, slides) -> an editable .pptx with real text boxes."""
    size = tuple(deck["size"])
    slides = [{**sl, "_size": size} for sl in deck["slides"]]
    data, _ = build_pptx(slides, deck["theme"], title, None, size=size, renderer=render_spec, thumbnail=thumbnail)
    return data


def fetch_images(slides: list[dict]) -> dict:
    """The pictures the user linked for figure slides (PNG/JPEG, up to 8 MB each)."""
    out = {}
    urls = {(s.get("figure") or {}).get("url", "") for s in slides if s.get("layout") == "figure"}
    with httpx.Client(timeout=20, follow_redirects=True, transport=designs.TRANSPORT, headers={"User-Agent": designs.UA}) as h:
        for u in [u for u in urls if u.startswith(("http://", "https://"))]:
            try:
                r = h.get(u)
            except httpx.HTTPError:
                continue
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and len(r.content) < 8_000_000 and ("png" in ct or "jpeg" in ct or "jpg" in ct):
                out[u] = (r.content, "png" if "png" in ct else "jpeg")
    return out


# ---------------------------------------------------------------- where the file goes
def drive_root() -> Path | None:
    """The "My Drive" folder of Google Drive for desktop on this computer, if it is installed and signed in."""
    env = os.environ.get("AFRA_GDRIVE_ROOT")
    if env:
        return Path(env) if Path(env).is_dir() else None
    from .sync import provider_roots
    roots = provider_roots().get("gdrive") or []
    return Path(roots[0]) if roots else None


def safe_name(name: str) -> str:
    return (re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name or "Deck").strip(" .") or "Deck")[:100]


def save(data: bytes, name: str, to_drive: bool = True) -> dict:
    """Write the deck into "My Drive/A.F.R.A Decks" when Drive for desktop is there, otherwise ~/AFRA-Slides/decks."""
    root = drive_root() if to_drive else None
    from .slides import FOLDER
    folder = (root / FOLDER_NAME) if root else (FOLDER / "decks")
    folder.mkdir(parents=True, exist_ok=True)
    base = safe_name(name)
    p = folder / f"{base}.pptx"
    k = 2
    while p.exists():
        p = folder / f"{base} ({k}).pptx"
        k += 1
    p.write_bytes(data)
    if root:
        note = ("Saved to your Google Drive folder (My Drive › A.F.R.A Decks). Google Drive for desktop uploads it; "
                "open it in Drive with Google Slides (File › Save as Google Slides makes it a real Slides deck).")
    else:
        note = "Saved on this computer. Google Drive for desktop isn't installed, so it isn't in Drive yet."
    return {"path": str(p), "folder": str(folder), "in_drive": bool(root), "note": note}
