"""Tests for offline OPSIN-based systematic name resolution.

These exercise the real OPSIN CLI (JRE + jar), not a mock -- skipped unless
that's already set up on this machine (chemdraw_connector.domain.naming
--doctor), so a bare `pytest` run never triggers the ~50MB JRE/jar download
over the network.
"""
import pytest

from chemdraw_connector.domain import naming

pytestmark = pytest.mark.skipif(
    not naming.JRE_BIN.exists() or not naming.OPSIN_JAR.exists(),
    reason="OPSIN not set up locally -- run "
           "`python -m chemdraw_connector.domain.naming --doctor` first",
)


def test_resolve_systematic_name():
    result = naming.resolve_name_offline("2-acetoxybenzoic acid")
    assert result["smiles"]
    assert result["inchi"].startswith("InChI=1S/")
    assert result["inchikey"] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
    assert result["formula"] == "C9H8O4"
    assert result["exact_mass"] == pytest.approx(180.042, abs=1e-3)


def test_resolve_trivial_name_raises():
    with pytest.raises(RuntimeError, match="systematic IUPAC"):
        naming.resolve_name_offline("aspirin")


def test_resolve_empty_name_raises():
    with pytest.raises(ValueError):
        naming.resolve_name_offline("")
    with pytest.raises(ValueError):
        naming.resolve_name_offline("   ")


def test_cache_roundtrip():
    first = naming.resolve_name_offline("ethanol")
    # Second call is served from the sqlite cache, which also carries a
    # created_at timestamp the first (freshly-parsed) result doesn't have.
    second = naming.resolve_name_offline("ethanol")
    assert second["smiles"] == first["smiles"]
    assert second["inchikey"] == first["inchikey"]
    cached = naming._cache_get_name("ethanol")
    assert cached is not None
    assert cached["smiles"] == first["smiles"]
