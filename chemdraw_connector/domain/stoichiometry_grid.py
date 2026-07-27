"""Stoichiometry grid bounds extraction from CDXML."""
from chemdraw_connector.domain import stoichiometry_cdxml as sc


def extract_stoichiometry_grid_bounds(cdxml_text):
    """Extract stoichiometry grid bounds from CDXML.
    
    Returns list of dicts with grid_id, bounds, and component bounds.
    """
    if not cdxml_text:
        return []
    
    grids = sc.parse_grids(cdxml_text)
    result = []
    for grid in grids:
        # Grid position is in the 'p' attribute (x,y top-left)
        # Need to get from CDXML directly since parse_grids doesn't return it
        pass
    return result


def get_stoichiometry_grids_from_cdxml(cdxml_text):
    """Parse CDXML for stoichiometry grids with full bounds info."""
    import xml.etree.ElementTree as ET
    
    if not cdxml_text:
        return []
    
    root = ET.fromstring(cdxml_text)
    grids = []
    
    for grid_elem in root.findall('.//stoichiometrygrid'):
        grid_id = grid_elem.get('id')
        p = grid_elem.get('p', '0 0')  # top-left position
        try:
            x, y = map(float, p.split())
        except (ValueError, AttributeError):
            x, y = 0.0, 0.0
        
        # Components have Width attribute but not explicit bounds
        # We can estimate from the grid's overall bounds
        # The grid's 'p' is top-left, width/height not directly in CDXML
        # But we can get from the COM object if needed
        
        components = []
        for comp_elem in grid_elem.findall('.//sgcomponent'):
            comp = {
                'component_id': comp_elem.get('id'),
                'component_reference_id': comp_elem.get('ComponentReferenceID'),
                'is_reactant': comp_elem.get('ComponentIsReactant') == 'yes',
                'is_header': comp_elem.get('ComponentIsHeader') == 'yes',
            }
            components.append(comp)
        
        grids.append({
            'grid_id': grid_id,
            'top_left': (x, y),
            'components': components,
        })
    
    return grids