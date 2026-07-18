"""Reading and setting the live ChemDraw selection."""
from .. import targets


class _Selection:
    def get_selection(self):
        def go():
            doc = self._doc()
            units = targets.resolve(doc, "selection", self._cache_for(doc))
            return {"selected": self._describe_units(doc, units)}
        return self._run(go)

    def select(self, object_id):
        def go():
            doc = self._doc()
            unit = targets.find_by_id(doc, object_id, self._cache_for(doc))
            targets.unit_objects(unit).Select()
            return {"selected": object_id}
        return self._run(go)
