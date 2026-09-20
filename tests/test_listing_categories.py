import unittest
from listing_categories import classify_category
from listing_scraper import _parse_location_line

class CategoryTests(unittest.TestCase):
    def test_source_categories(self):
        groups = {
            'Apartments': ('apartment', ['Rent', 'Sublet', 'Sale']),
            'Houses': ('house', ['Rent', 'Sublet', 'Sale']),
            'Rooms': ('room', ['Rent', 'Sublet']),
            'Art Studios': ('art_studio', ['Rent', 'Share', 'Sublet']),
            'Artist Live/Work Spaces': ('live_work', ['Rent', 'Sublet']),
        }
        for prefix, (space, suffixes) in groups.items():
            for suffix in suffixes:
                with self.subTest(category=f'{prefix} for {suffix}'):
                    self.assertEqual(classify_category(f'{prefix} for {suffix}'),
                        (space, 'buy' if suffix == 'Sale' else suffix.lower(), 'available'))
        for source, expected in {
            'Commercial Spaces': ('commercial', 'unspecified', 'available'),
            'Event Spaces': ('event', 'unspecified', 'available'),
            'Medical & Wellness Spaces': ('wellness', 'unspecified', 'available'),
            'Swap': ('unspecified', 'swap', 'available'),
            'House Sitting': ('unspecified', 'house_sitting', 'available'),
            'Seeking Living Space': ('unspecified', 'unspecified', 'wanted'),
            'Prospect Heights': ('unspecified', 'unspecified', 'unknown'),
            'New future category': ('unspecified', 'unspecified', 'unknown'),
            '': ('unspecified', 'unspecified', 'unknown'),
        }.items():
            with self.subTest(category=source):
                self.assertEqual(classify_category(source), expected)

    def test_multiple_location_segments_do_not_become_category(self):
        result = _parse_location_line('Crown Heights | Prospect Heights, Brooklyn | Seeking Living Space')
        self.assertEqual(result[3], 'Seeking Living Space')
        self.assertIn('Prospect Heights', result[1])
        self.assertIn('Crown Heights', result[1])
        self.assertEqual(result[4], 'brooklyn')
        self.assertEqual(_parse_location_line('Paris | New category', 'paris')[3], 'New category')
