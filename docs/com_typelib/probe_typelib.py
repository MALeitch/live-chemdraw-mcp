"""Dump every interface/method/property in the live ChemDraw COM type
library, regardless of whether the connector has touched it yet.

Regenerate after a ChemDraw version bump:
    .venv\\Scripts\\python.exe docs\\com_typelib\\probe_typelib.py \\
        > docs\\com_typelib\\typelib_dump_<date>.txt

Requires a running, visible ChemDraw instance (attaches via GetActiveObject
-- never Dispatch(), which could launch a second ChemDraw.exe process
alongside whatever the user already has open)."""
import win32com.client
import sys

app = win32com.client.GetActiveObject("ChemDraw_x64.Application")

# Get the typelib straight off the Application's own TypeInfo.
disp = app._oleobj_
typeinfo = disp.GetTypeInfo()
typelib, index = typeinfo.GetContainingTypeLib()
la = typelib.GetLibAttr()
print(f"TypeLib GUID={la[0]} lcid={la[1]} ver={la[3]}.{la[4]}", file=sys.stderr)

n = typelib.GetTypeInfoCount()
print(f"Total type infos: {n}", file=sys.stderr)

out = []
for i in range(n):
    ti = typelib.GetTypeInfo(i)
    tname, doc, helpctx, helpfile = typelib.GetDocumentation(i)
    attr = ti.GetTypeAttr()
    kind = attr.typekind  # 0=interface enum, see TYPEKIND
    members = []
    try:
        nfuncs = attr.cFuncs
        for f in range(nfuncs):
            fdesc = ti.GetFuncDesc(f)
            fname = ti.GetNames(fdesc.memid)[0]
            invkind = fdesc.invkind
            # 1=METHOD,2=PROPERTYGET,4=PROPERTYPUT,8=PROPERTYPUTREF
            tag = {1: "method", 2: "get", 4: "put", 8: "putref"}.get(invkind, str(invkind))
            members.append(f"{tag}:{fname}")
    except Exception as e:
        members.append(f"<error reading funcs: {e}>")
    out.append((tname, doc or "", kind, members))

for tname, doc, kind, members in out:
    print(f"=== {tname} (kind={kind}) === {doc}")
    # dedupe get/put pairs for readability
    seen = set()
    for m in members:
        tag, name = m.split(":", 1)
        key = name
        if key in seen and tag in ("put", "putref", "get"):
            continue
        seen.add(key)
        print(f"    {m}")
