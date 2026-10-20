from gamedata.services.market_sectors import FALLBACK_SECTOR, SECTORS, sector_of


def test_every_category_maps_to_one_sector() -> None:
    categories = [category for _, listed in SECTORS for category in listed]

    assert len(categories) == len(set(categories))
    assert sector_of('metals') == 'metals_alloys'
    assert sector_of('ship shields') == 'ships_fuel'
    assert sector_of('something new') == FALLBACK_SECTOR
