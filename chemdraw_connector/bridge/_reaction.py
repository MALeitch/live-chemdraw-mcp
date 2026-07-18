"""Reaction scheme layout: reactants + arrow + products."""
from .. import targets
from ._plumbing import SLOW_TIMEOUT


class _Reaction:
    def make_reaction_scheme(self, reactants, products, reagents_text=None,
                             fmt="smiles"):
        reactants = [self._validate_input(r, fmt) for r in reactants]
        products = [self._validate_input(p, fmt) for p in products]

        def go():
            doc = self._doc()
            backup = self._maybe_snapshot(doc)
            x, y = 60.0, 120.0
            gap = 24.0
            ids = []

            def place(rep):
                nonlocal x
                units = self._insert_structure_units(doc, rep, fmt)
                for u in units:
                    objs = targets.unit_objects(u)
                    objs.Move(x - u.Left, y - (u.Top + u.Bottom) / 2.0)
                    ids.append(targets.ensure_id(u))
                    x = u.Right + gap

            def plus():
                nonlocal x
                cap = doc.MakeCaption()
                cap.Text = "+"
                self._set_position(cap, x, y)
                x += 18.0

            for i, r in enumerate(reactants):
                if i:
                    plus()
                place(r)

            arrow_len = 70.0
            arrow_ok = False
            try:
                arrow = doc.MakeArrow()
                self._set_position(arrow, x + arrow_len / 2.0, y)
                arrow_ok = True
            except Exception:
                cap = doc.MakeCaption()
                cap.Text = "→"
                self._set_position(cap, x + arrow_len / 2.0, y)
            if reagents_text:
                cap = doc.MakeCaption()
                cap.Text = reagents_text
                self._set_position(cap, x + arrow_len / 2.0, y - 24.0)
            x += arrow_len + gap

            for i, p in enumerate(products):
                if i:
                    plus()
                place(p)

            return {
                "object_ids": ids,
                "arrow_native": arrow_ok,
                "backup_path": backup,
                "preview_png_base64": self._preview_png(doc),
            }
        return self._run(go, timeout=SLOW_TIMEOUT)
