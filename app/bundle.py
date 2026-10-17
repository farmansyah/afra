"""Share one project as a file (.afra): its papers with notes and quotes, folders, PDFs, documents and research runs.

Send the file to a classmate (WhatsApp, email, a flash drive). Importing it adds the project as a new project, so
nothing in the receiver's library is overwritten, even if they import the same file twice.
"""
import io
import json
import time
import zipfile

from . import db

FORMAT = 1


def export_project(pid: str) -> tuple[bytes, str]:
    with db.conn() as c:
        proj = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not proj:
            raise ValueError("Project not found")
        rows = lambda q: [dict(r) for r in c.execute(q, (pid,))]
        data = {
            "format": FORMAT, "exported": time.time(), "project": dict(proj),
            "papers": rows("SELECT * FROM papers WHERE project_id=?"),
            "documents": rows("SELECT * FROM documents WHERE project_id=?"),
            "runs": rows("SELECT * FROM runs WHERE project_id=?"),
            "collections": rows("SELECT * FROM collections WHERE project_id=?"),
            "paper_collections": rows("""SELECT pc.* FROM paper_collections pc JOIN collections k ON k.id=pc.collection_id
                                         WHERE k.project_id=?"""),
            "quotes": rows("SELECT * FROM quotes WHERE project_id=?"),
        }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(data, ensure_ascii=False))
        for p in data["papers"]:
            if db.pdf_path(p["id"]).exists():
                z.write(db.pdf_path(p["id"]), f"pdfs/{p['id']}.pdf")
    return buf.getvalue(), proj["name"]


def import_project(blob: bytes) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        data = json.loads(z.read("project.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise ValueError("This is not an A.F.R.A project file (.afra).")
    if data.get("format", 0) > FORMAT:
        raise ValueError("This project file comes from a newer A.F.R.A. Update A.F.R.A first.")
    db.backup("before-import-project")
    ids = {}
    new = lambda old: ids.setdefault(old, db.new_id())

    def insert(c, table, row, **changes):
        row = {**row, **changes}
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})") if r[1] in row]
        c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[k] for k in cols])

    p = data["project"]
    names = {x["name"] for x in db.list_projects()}
    name = p["name"] if p["name"] not in names else f"{p['name']} (imported {time.strftime('%d %b %Y')})"
    pid = new(p["id"])
    with db.conn() as c:
        insert(c, "projects", p, id=pid, name=name, created=time.time())
        for x in data.get("papers", []):
            insert(c, "papers", x, id=new(x["id"]), project_id=pid)
        for x in data.get("documents", []):
            insert(c, "documents", x, id=new(x["id"]), project_id=pid)
        for x in data.get("runs", []):
            insert(c, "runs", x, id=new(x["id"]), project_id=pid)
        for x in data.get("collections", []):
            insert(c, "collections", x, id=new(x["id"]), project_id=pid)
        for x in data.get("paper_collections", []):
            if x["paper_id"] in ids and x["collection_id"] in ids:
                insert(c, "paper_collections", x, paper_id=ids[x["paper_id"]], collection_id=ids[x["collection_id"]])
        for x in data.get("quotes", []):
            if x["paper_id"] in ids:
                insert(c, "quotes", x, id=new(x["id"]), paper_id=ids[x["paper_id"]], project_id=pid)
    db.PDF_DIR.mkdir(parents=True, exist_ok=True)
    for n in z.namelist():
        old = n.removeprefix("pdfs/").removesuffix(".pdf")
        if n.startswith("pdfs/") and old in ids:
            db.pdf_path(ids[old]).write_bytes(z.read(n))
    db.DIRTY.add(pid)
    return {"id": pid, "name": name, "papers": len(data.get("papers", [])), "documents": len(data.get("documents", []))}
