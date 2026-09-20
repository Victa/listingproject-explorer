"""Source category classification shared by parsing and search."""
from typing import NamedTuple

SPACE_TYPES = {
    'apartment': 'Apartment', 'house': 'House', 'room': 'Room',
    'art_studio': 'Art studio', 'live_work': 'Artist live/work',
    'commercial': 'Commercial', 'event': 'Event',
    'wellness': 'Medical & wellness', 'unspecified': 'Other / unspecified',
}
ARRANGEMENTS = {
    'rent': 'Rent', 'buy': 'Buy', 'sublet': 'Sublet', 'share': 'Share',
    'swap': 'Swap', 'house_sitting': 'House sitting', 'unspecified': 'Unspecified',
}
POST_KINDS = {'all': 'All posts', 'available': 'Available spaces', 'wanted': 'Wanted posts'}

class Classification(NamedTuple):
    space_type: str
    arrangement: str
    post_kind: str

_CATEGORIES = {}
for source, space in [('Apartments', 'apartment'), ('Houses', 'house'),
                      ('Rooms', 'room'), ('Art Studios', 'art_studio'),
                      ('Artist Live/Work Spaces', 'live_work')]:
    for suffix, arrangement in [('Rent', 'rent'), ('Sale', 'buy'), ('Sublet', 'sublet'), ('Share', 'share')]:
        _CATEGORIES[f'{source} for {suffix}'.casefold()] = Classification(space, arrangement, 'available')
for source, space in [('Commercial Spaces', 'commercial'), ('Event Spaces', 'event'),
                      ('Medical & Wellness Spaces', 'wellness')]:
    _CATEGORIES[source.casefold()] = Classification(space, 'unspecified', 'available')
_CATEGORIES.update({
    'swap': Classification('unspecified', 'swap', 'available'),
    'house sitting': Classification('unspecified', 'house_sitting', 'available'),
    'seeking living space': Classification('unspecified', 'unspecified', 'wanted'),
})

def classify_category(category: str) -> Classification:
    return _CATEGORIES.get(' '.join((category or '').split()).casefold(),
                           Classification('unspecified', 'unspecified', 'unknown'))

def migrate_category_filters(raw: dict) -> dict:
    """Convert exact source categories into independent facets once."""
    result = dict(raw)
    if raw.get('filter_version', 0) < 2:
        categories = raw.get('property_types', [])
        facets = [classify_category(c) for c in categories if isinstance(c, str)] if isinstance(categories, list) else []
        result.update(space_types=sorted({f.space_type for f in facets}),
                      arrangements=sorted({f.arrangement for f in facets}), post_kind='all')
        kinds = {f.post_kind for f in facets}
        if len(kinds) == 1 and 'unknown' not in kinds:
            result['post_kind'] = next(iter(kinds))
    result.pop('property_types', None)
    result['filter_version'] = 2
    return result
