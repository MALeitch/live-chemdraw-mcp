"""Deep-dump specific interfaces/enums from the live ChemDraw type library --
full method/property signatures for interfaces, resolved member values for
enums. Grow TARGETS across future probe sessions rather than rewriting it;
each new spike should add whatever interfaces/enums it needs to look up.

Regenerate after adding to TARGETS (or after a ChemDraw version bump):
    .venv\\Scripts\\python.exe docs\\com_typelib\\probe_typelib2.py \\
        > docs\\com_typelib\\typelib_dump2_<date>.txt

Requires a running, visible ChemDraw instance (attaches via GetActiveObject
-- never Dispatch(), which could launch a second ChemDraw.exe process
alongside whatever the user already has open)."""
import win32com.client

app = win32com.client.GetActiveObject("ChemDraw_x64.Application")
disp = app._oleobj_
typeinfo = disp.GetTypeInfo()
typelib, _ = typeinfo.GetContainingTypeLib()
n = typelib.GetTypeInfoCount()

TARGETS = {
    "IChemDrawStoichiometryGrid", "IChemDrawSGComponent", "IChemDrawSGProperty",
    "IChemDrawTLCPlate", "IChemDrawTLCLane", "IChemDrawTLCSpot",
    "IChemDrawTable", "IChemDrawCell", "IChemDrawBorder",
    "IChemDrawBracket", "IChemDrawFragmentationAnalyzer", "IChemDrawMassFragment",
    "IChemDrawFragmentationLine", "IChemDrawAltGroup", "IChemDrawAnnotation",
    "IChemDrawSymbol", "IChemDrawSpline", "IChemDrawGeometry", "IChemDrawConstraint",
    "IChemDrawReactionScheme", "IChemDrawReactionStep",
    "CDArrowType", "CDEnhancedStereoType", "CDSymbolType", "CDBracketType",
    "CDBracketUsage", "CDPolymerRepeatPattern", "CDGeometryType", "CDConstraintType",
    "MenuBars", "Menu", "MenuItem", "MenuItems",
    "IChemDrawAnnotations",
}

by_name = {}
for i in range(n):
    ti = typelib.GetTypeInfo(i)
    tname, doc, _, _ = typelib.GetDocumentation(i)
    by_name[tname] = (ti, doc)

for name in TARGETS:
    if name not in by_name:
        print(f"### {name}: NOT FOUND")
        continue
    ti, doc = by_name[name]
    attr = ti.GetTypeAttr()
    print(f"\n### {name} (kind={attr.typekind}) -- {doc}")
    if attr.typekind == 0:  # enum
        for v in range(attr.cVars):
            vd = ti.GetVarDesc(v)
            vname = ti.GetNames(vd.memid)[0]
            val = getattr(vd, "value", None)
            if val is None:
                for attrname in ("lpvarValue", "varValue"):
                    val = getattr(vd, attrname, None)
                    if val is not None:
                        break
            print(f"    {vname} = {val!r}")
        continue
    for f in range(attr.cFuncs):
        try:
            fdesc = ti.GetFuncDesc(f)
            names = ti.GetNames(fdesc.memid)
            fname = names[0]
            args = names[1:] if len(names) > 1 else []
            invkind = fdesc.invkind
            tag = {1: "method", 2: "get", 4: "put", 8: "putref"}.get(invkind, str(invkind))
            print(f"    {tag}: {fname}({', '.join(args)})")
        except Exception as e:
            print(f"    <error at func {f}: {e}>")
