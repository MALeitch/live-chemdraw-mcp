"""Offline chemical name resolution using OPSIN (Open Parser for Systematic IUPAC Nomenclature).

OPSIN: https://opsin.ch.cam.ac.uk/
GitHub: https://github.com/dan2097/opsin

This module bundles a JRE and the OPSIN CLI jar on first use (like cdxml-toolkit's
cdxml-doctor), then provides fast offline systematic IUPAC name → SMILES/InChI
conversion with local caching.

NOTE: OPSIN ONLY parses systematic IUPAC names, NOT trivial/common names
(like "aspirin", "caffeine", "glucose"). For common names, use ChemDraw's
built-in resolver (format="name" in chemdraw_insert_structure) which queries
PubChem online.
"""

import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & Constants
# ---------------------------------------------------------------------------

# Where we store JRE, OPSIN jar, and cache
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "chemdraw-mcp" / "opsin"
JRE_DIR = CACHE_DIR / "jre"
JRE_BIN = JRE_DIR / "bin" / "java.exe"
OPSIN_JAR = CACHE_DIR / "opsin-cli.jar"

# OPSIN release to use
OPSIN_VERSION = "2.9.0"
OPSIN_JAR_URL = (
    f"https://github.com/dan2097/opsin/releases/download/{OPSIN_VERSION}/"
    f"opsin-cli-{OPSIN_VERSION}-jar-with-dependencies.jar"
)

# JRE download (Eclipse Temurin 21, Windows x64 JRE) - known good release
JRE_URL = (
    "https://github.com/adoptium/temurin21-binaries/releases/download/"
    "jdk-21.0.6%2B7/OpenJDK21U-jre_x64_windows_hotspot_21.0.6_7.zip"
)

# Cache database
CACHE_DB = CACHE_DIR / "name_cache.sqlite"


# ---------------------------------------------------------------------------
# Setup / Doctor
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: Path, desc: str) -> None:
    """Download a file with progress."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {desc} from {url}...", file=sys.stderr)

    def reporthook(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100, block_num * block_size * 100 // total_size)
            print(f"\r  {pct}%", end="", file=sys.stderr)

    urllib.request.urlretrieve(url, dest, reporthook)
    print(f"\r  Done. Saved to {dest}", file=sys.stderr)


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract a zip file."""
    import zipfile

    print(f"Extracting {zip_path.name} to {dest_dir}...", file=sys.stderr)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    print("Extraction complete.", file=sys.stderr)


def _find_java_in_extracted(dest_dir: Path) -> Path | None:
    """Find java.exe in the extracted JRE directory."""
    for java_exe in dest_dir.rglob("java.exe"):
        return java_exe
    return None


def ensure_jre_ready() -> Path:
    """Ensure a JRE is available at JRE_BIN. Downloads and extracts if missing."""
    if JRE_BIN.exists():
        return JRE_BIN

    print("JRE not found. Downloading Eclipse Temurin JRE...", file=sys.stderr)
    zip_path = CACHE_DIR / "jre.zip"
    _download_file(JRE_URL, zip_path, "JRE")

    # Extract to a temp subdir, then move the JRE folder to our target location
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _extract_zip(zip_path, tmp_path)
        java_exe = _find_java_in_extracted(tmp_path)
        if not java_exe:
            raise RuntimeError("JRE extraction succeeded but java.exe not found")
        # Move the parent JRE folder to our target location
        jre_root = java_exe.parent.parent  # .../jre/bin/java.exe -> .../jre
        if JRE_DIR.exists():
            import shutil
            shutil.rmtree(JRE_DIR)
        import shutil
        shutil.move(str(jre_root), str(JRE_DIR))

    zip_path.unlink(missing_ok=True)

    if not JRE_BIN.exists():
        raise RuntimeError(f"JRE setup failed: {JRE_BIN} not found after extraction")

    # Test it
    result = subprocess.run([str(JRE_BIN), "-version"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"JRE test failed: {result.stderr}")

    print(f"JRE ready at {JRE_BIN}", file=sys.stderr)
    return JRE_BIN


def ensure_opsin_jar_ready() -> Path:
    """Ensure OPSIN CLI jar is downloaded."""
    if OPSIN_JAR.exists():
        return OPSIN_JAR

    print("OPSIN jar not found. Downloading...", file=sys.stderr)
    _download_file(OPSIN_JAR_URL, OPSIN_JAR, "OPSIN CLI jar")

    # Test it
    java = ensure_jre_ready()
    result = subprocess.run(
        [str(java), "-jar", str(OPSIN_JAR), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"OPSIN jar test failed: {result.stderr}")

    print(f"OPSIN jar ready at {OPSIN_JAR}", file=sys.stderr)
    return OPSIN_JAR


def ensure_opsin_ready() -> dict[str, Any]:
    """Run full setup: ensure JRE + OPSIN jar are present and working."""
    java = ensure_jre_ready()
    jar = ensure_opsin_jar_ready()
    return {
        "java": str(java),
        "opsin_jar": str(jar),
        "cache_dir": str(CACHE_DIR),
        "cache_db": str(CACHE_DB),
    }


# ---------------------------------------------------------------------------
# Local Cache (SQLite)
# ---------------------------------------------------------------------------

def _init_cache_db() -> None:
    """Initialize the cache database."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CACHE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS name_cache (
                name TEXT PRIMARY KEY,
                smiles TEXT NOT NULL,
                inchi TEXT,
                inchikey TEXT,
                formula TEXT,
                exact_mass REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _cache_get_name(name: str) -> dict | None:
    """Look up a name in the cache."""
    _init_cache_db()
    with sqlite3.connect(CACHE_DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM name_cache WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return dict(row)
    return None


def _cache_put_name(data: dict) -> None:
    """Store a name resolution in the cache."""
    _init_cache_db()
    with sqlite3.connect(CACHE_DB) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO name_cache
            (name, smiles, inchi, inchikey, formula, exact_mass)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data["smiles"], data.get("inchi"),
            data.get("inchikey"), data.get("formula"), data.get("exact_mass")
        ))


# ---------------------------------------------------------------------------
# OPSIN CLI Wrapper
# ---------------------------------------------------------------------------

def _run_opsin(args: list[str], input_text: str = "") -> str:
    """Run OPSIN CLI with given args and optional stdin input."""
    java = ensure_jre_ready()
    jar = ensure_opsin_jar_ready()

    cmd = [str(java), "-jar", str(jar), *args]
    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"OPSIN failed (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def _run_opsin_multi(name: str) -> dict:
    """Run OPSIN multiple times to get all desired formats."""
    # Run once for SMILES
    smiles = _run_opsin(["-osmi"], input_text=name).strip()
    
    # Run for InChI (standard, to match RDKit's default InChI=1S/ used
    # elsewhere in this codebase, e.g. domain/dedup.py's InChIKey matching --
    # OPSIN's plain "-oinchi" gives the older non-standard FixedH variant)
    inchi = _run_opsin(["-ostdinchi"], input_text=name).strip()
    
    # Run for InChIKey
    inchikey = _run_opsin(["-ostdinchikey"], input_text=name).strip()
    
    # Compute formula and exact mass from SMILES using RDKit
    formula = None
    exact_mass = None
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, rdMolDescriptors
        mol = Chem.MolFromSmiles(smiles)
        if mol:
            formula = rdMolDescriptors.CalcMolFormula(mol)
            exact_mass = Descriptors.ExactMolWt(mol)
    except Exception:
        pass
    
    return {
        "smiles": smiles,
        "inchi": inchi if inchi.startswith("InChI=") else None,
        "inchikey": inchikey if len(inchikey) == 27 and inchikey.count("-") == 2 else None,
        "formula": formula,
        "exact_mass": exact_mass,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_name_offline(name: str) -> dict:
    """Resolve a SYSTEMATIC IUPAC chemical name to structure data using offline OPSIN.

    Returns dict with: name, smiles, inchi, inchikey, formula, exact_mass.
    Raises on failure (caller should catch and fall back to ChemDraw's resolver).

    IMPORTANT: OPSIN only understands SYSTEMATIC IUPAC names, NOT trivial/common
    names. Examples:
      - WORKS: "2-acetoxybenzoic acid", "methane", "ethanol", "benzene"
      - FAILS: "aspirin", "caffeine", "glucose", "Tylenol", "Vitamin C"
    
    For common/trivial names, use ChemDraw's built-in resolver via:
      chemdraw_insert_structure(representation="aspirin", format="name")
    """
    name = name.strip()
    if not name:
        raise ValueError("Empty name")

    # Check cache first
    cached = _cache_get_name(name)
    if cached:
        return {"name": name, **cached}

    # Run OPSIN multiple times for all formats
    parsed = _run_opsin_multi(name)
    if not parsed.get("smiles"):
        raise RuntimeError(
            f"OPSIN could not parse '{name}'. "
            f"OPSIN only handles systematic IUPAC names, not common/trivial names. "
            f"Try ChemDraw's built-in resolver (format='name') for common names."
        )

    result = {"name": name, **parsed}
    _cache_put_name(result)
    return result


# ---------------------------------------------------------------------------
# Module-level initialization (optional, can be called eagerly)
# ---------------------------------------------------------------------------

def initialize() -> dict[str, Any]:
    """Initialize OPSIN (download JRE + jar if needed). Returns status dict."""
    return ensure_opsin_ready()


# ---------------------------------------------------------------------------
# CLI entry point for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OPSIN offline systematic name resolver")
    parser.add_argument("name", nargs="?", help="Systematic IUPAC name to resolve")
    parser.add_argument("--doctor", action="store_true", help="Run setup/doctor")
    parser.add_argument("--test", action="store_true", help="Run a quick test (systematic name)")

    args = parser.parse_args()

    if args.doctor:
        status = ensure_opsin_ready()
        print(json.dumps(status, indent=2))
        sys.exit(0)

    if args.test:
        status = ensure_opsin_ready()
        print("Setup OK:", json.dumps(status, indent=2))
        result = resolve_name_offline("2-acetoxybenzoic acid")
        print("Test resolve '2-acetoxybenzoic acid':", json.dumps(result, indent=2))
        sys.exit(0)

    if args.name:
        result = resolve_name_offline(args.name)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    parser.print_help()