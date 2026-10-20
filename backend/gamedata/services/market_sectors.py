# Sector keys of the public market report and their material categories (GameMaterial.category_name).
# Labels live in the frontend's i18n. A category not listed here falls into drones_other.
SECTORS: list[tuple[str, list[str]]] = [
    ('consumables', ['consumable bundles', 'consumables (basic)', 'consumables (luxury)']),
    (
        'construction',
        ['construction materials', 'construction parts', 'construction prefabs', 'infrastructure', 'unit prefabs'],
    ),
    ('raw_resources', ['elements', 'gases', 'liquids', 'minerals', 'ores']),
    ('metals_alloys', ['alloys', 'metals']),
    ('agri_chemicals', ['agricultural products', 'chemicals', 'plastics', 'textiles']),
    (
        'electronics_software',
        [
            'electronic devices',
            'electronic parts',
            'electronic pieces',
            'electronic systems',
            'energy systems',
            'software components',
            'software systems',
            'software tools',
        ],
    ),
    ('ships_fuel', ['fuels', 'ship engines', 'ship kits', 'ship parts', 'ship shields']),
    ('drones_other', ['drones', 'medical equipment', 'utility']),
]
FALLBACK_SECTOR = 'drones_other'

_SECTOR_OF_CATEGORY = {category: key for key, categories in SECTORS for category in categories}


def sector_of(category: str) -> str:
    return _SECTOR_OF_CATEGORY.get(category, FALLBACK_SECTOR)
