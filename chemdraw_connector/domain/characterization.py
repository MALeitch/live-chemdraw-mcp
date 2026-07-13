"""HRMS-style characterization text from formula + monoisotopic mass."""

PROTON = 1.007276
_ADDUCTS = {
    "[M+H]+": PROTON,
    "[M+Na]+": 22.989218,
    "[M+K]+": 38.963158,
    "[M+NH4]+": 18.033823,
    "[M-H]-": -PROTON,
    "[M]+": -0.000549,   # loss of one electron
}


def adduct_names():
    return sorted(_ADDUCTS)


def hrms_line(formula, exact_mass, ion_mode="[M+H]+", technique="ESI",
              found=None):
    """e.g. HRMS (ESI) m/z calc'd for C9H8O4 [M+H]+: 181.0495, found: ____."""
    try:
        delta = _ADDUCTS[ion_mode]
    except KeyError:
        raise ValueError(
            f"Unknown ion mode {ion_mode!r}; available: {adduct_names()}"
        ) from None
    mz = float(exact_mass) + delta
    found_text = f"{found:.4f}" if isinstance(found, (int, float)) else "____"
    return (
        f"HRMS ({technique}) m/z calc'd for {formula} {ion_mode}: "
        f"{mz:.4f}, found: {found_text}."
    )
