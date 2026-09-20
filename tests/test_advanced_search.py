from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import pickle
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from listing_scraper import (
    NYC_REGION, REGIONS_URL, Region, ListingRow, discover_regions,
    fetch_region_listings, merge_listings, parse_listings_from_html,
)
from listings_store import ListingsStore, access_key

ROOT = Path(__file__).resolve().parents[1]
PARIS = Region('paris', 'Paris', REGIONS_URL + '/paris')
NYC = Region(NYC_REGION, 'New York City', REGIONS_URL + '/' + NYC_REGION)


def card(url='/listings/example', location='Le Marais, Paris', price='€1,250/month', dates='September 20, 2026 - October 20, 2026'):
    return f'''<div class="flex flex-col md:flex-row mb-8">
    <div class="text-grey-dark text-smish">{location} | Apartments for Sublet</div>
    <a class="text-teal-light hover:text-teal no-underline" href="{url}">A place to stay</a>
    <span>{price}</span><span>{dates}</span><p class="text-sm leading-normal mb-4">A bright home</p></div>'''


def index(cards, count=1, pages=''):
    return f'<h1><strong>{count} Listings</strong></h1>{cards}{pages}'


def listing(region=PARIS, url='https://www.listingsproject.com/listings/example', area='Le Marais'):
    return ListingRow('A place to stay', area, area, (area,), '', 'all', 'Apartments for Sublet',
                      'A bright home', None, 'Dates', datetime(2026, 9, 20), datetime(2026, 10, 20),
                      '€1,250/month', url, region_key=region.key, region_label=region.label, region_keys=(region.key,))


# Load reusable UI functions without executing the Streamlit page.
UI = types.ModuleType('ui_helpers')
UI.__file__ = str(ROOT / 'app.py')
exec(compile((ROOT / 'app.py').read_text().split('st.set_page_config(')[0], UI.__file__, 'exec'), UI.__dict__)


class ParserTests(unittest.TestCase):
    def test_dynamic_discovery_tiles_text_and_foreign_links(self):
        html = '''<a href="/real-estate/paris"><span>Paris</span></a>
          <a href="/real-estate/new-region">New &amp; Exciting</a>
          <a href="/real-estate/paris/sublets">Sublets</a>
          <a href="/real-estate/first-access">First access</a>
          <a href="https://other.example/real-estate/rogue">Rogue</a>'''
        self.assertEqual([(r.key,r.label) for r in discover_regions(html)], [('new-region','New & Exciting'),('paris','Paris')])

    def test_first_access_can_discover_regions_from_category_links(self):
        regions=discover_regions('<a href="/real-estate/first-access/paris/sublets">Sublets</a>',first_access=True)
        self.assertEqual(regions,[Region('paris','Paris',REGIONS_URL+'/first-access/paris')])

    def test_international_area_currency_and_missing_dates(self):
        rows = parse_listings_from_html(card(dates='Available by arrangement'), region=PARIS)
        self.assertEqual(rows[0]['neighborhood_names'], ('Le Marais',))
        self.assertEqual(rows[0]['borough_key'], 'all')
        self.assertEqual(rows[0]['price'], '€1,250/month')
        self.assertIsNone(rows[0]['listing_start'])

    def test_single_date_does_not_invent_one_day_stay(self):
        row = parse_listings_from_html(card(dates='November 1, 2026'), region=PARIS)[0]
        self.assertIsNotNone(row['listing_start'])
        self.assertIsNone(row['listing_end'])

    def test_incomplete_cards_reject_snapshot(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200,text=index(card(),2)))) as client:
            with self.assertRaises(ValueError): fetch_region_listings(PARIS,client=client)

    def test_nyc_aliases_still_normalize(self):
        row = parse_listings_from_html(card(location='Bed Stuy, Brooklyn'), region=NYC)[0]
        self.assertIn('Bedford-Stuyvesant', row['neighborhood_names'])
        self.assertEqual(row['borough_key'], 'brooklyn')

    def test_non_nyc_names_are_not_mistaken_for_boroughs(self):
        row = parse_listings_from_html(card(location='Chelsea, New York'), region=PARIS)[0]
        self.assertEqual(row['borough_key'], 'all')
        self.assertEqual(row['neighborhood_names'], ('Chelsea',))

    def test_merge_preserves_regions_and_first_access(self):
        a = replace(listing(), is_first_access=True)
        b = listing(NYC)
        rows = merge_listings([a,b])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].is_first_access)
        self.assertEqual(set(rows[0].region_keys), {'paris', NYC_REGION})

    def test_pagination_is_complete(self):
        def respond(request):
            page = request.url.params.get('page')
            return httpx.Response(200, text=index(card('/listings/'+page),2,'<a href="?page=2">2</a>') if page=='1' else index(card('/listings/2'),2))
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            rows=fetch_region_listings(PARIS,client=client,request_delay_s=0)
        self.assertEqual(len(rows),2)

    def test_failed_second_page_rejects_snapshot(self):
        def respond(request):
            return httpx.Response(404) if request.url.params.get('page') == '2' else httpx.Response(200,text=index(card(),2,'<a href="?page=2">2</a>'))
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                fetch_region_listings(PARIS,client=client,request_delay_s=0)

    def test_unrecognized_markup_is_not_empty_success(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,text='<h1>Sign in</h1>'))) as client:
            with self.assertRaises(ValueError): fetch_region_listings(PARIS,client=client)

    def test_zero_listings_is_valid(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=index('',0)))) as client:
            self.assertEqual(fetch_region_listings(PARIS,client=client),[])

    def test_first_access_categories_are_discovered(self):
        region=Region('paris','Paris',REGIONS_URL+'/first-access/paris')
        def respond(request):
            text=(f'<a href="{region.url}/sublets">Sublets</a>' if request.url.path.endswith('/paris') else index(card()))
            return httpx.Response(200,text=text)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            rows=fetch_region_listings(region,client=client,first_access=True,request_delay_s=0)
        self.assertTrue(rows[0].is_first_access)


class FilterTests(unittest.TestCase):
    def filter(self, rows, **kwargs):
        args=dict(region_keys=set(),borough_keys=set(),neighborhoods=set(),property_types=set(),
                  first_access_only=False,new_only=False,new_urls=set(),date_mode='none',dates_start=None,
                  dates_end=None,dates_tolerance_days=0,flexible_stay='week',flexible_months=[])
        args.update(kwargs)
        return UI.filter_rows(rows,**args)

    def test_same_area_name_different_regions(self):
        rows=[listing(PARIS,area='Downtown'),listing(NYC,url='https://example.com/nyc',area='Downtown')]
        self.assertEqual(self.filter(rows,neighborhoods={'paris::Downtown'}),[rows[0]])
        self.assertEqual(self.filter(rows,region_keys={NYC_REGION}),[rows[1]])

    def test_unknown_dates_only_in_any_dates(self):
        row=replace(listing(),listing_start=None,listing_end=None)
        self.assertEqual(self.filter([row]),[row])
        self.assertEqual(self.filter([row],date_mode='dates'),[])
        self.assertIn('not specified', UI._format_card_dates_html(row))

    def test_migrate_saved_search_and_clear_defaults(self):
        filters=UI._validate_filters({'borough_keys':['brooklyn'],'neighborhoods':['Bedford-Stuyvesant']})
        self.assertEqual(filters['region_keys'],[NYC_REGION])
        self.assertEqual(filters['neighborhoods'],[NYC_REGION+'::Bedford-Stuyvesant'])
        self.assertEqual(UI._default_filters()['region_keys'],[])
        self.assertEqual(UI._validate_filters({'region_keys':['paris'],'borough_keys':['brooklyn']})['borough_keys'],[])

    def test_unspecified_price_period_is_not_labeled_monthly(self):
        self.assertNotIn('monthly', UI._format_price_html('$675'))

    def test_currency_preserved(self):
        row=replace(listing(),price='£125/night',listing_end=datetime(2026,12,1))
        self.assertEqual(UI._display_price_for_row(row),'£125/night')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.stores=[]

    def tearDown(self):
        for store in self.stores: store.executor.shutdown(wait=True)
        self.temp.cleanup()

    def store(self,cookie=None):
        store=ListingsStore(self.directory,cookie)
        self.stores.append(store)
        return store

    def test_stale_cache_load_and_access_isolation(self):
        store=self.store('private')
        store.regions={'paris':PARIS}
        store.sources={'public:paris':{'fetched_at':1,'rows':[listing()]}}
        store._save_locked()
        self.assertEqual(len(self.store('private').snapshot()['rows']),1)
        self.assertEqual(self.store().snapshot()['rows'],[])
        self.assertFalse(store.path.with_suffix('.tmp').exists())

    def test_legacy_cache_migrates_as_nyc(self):
        row=listing(NYC)
        # Simulate an old dataclass pickle with no regional attributes.
        for field in ('region_key','region_label','region_keys'): object.__delattr__(row,field)
        (self.directory/'.listings_cache.pkl').write_bytes(pickle.dumps({
            'cookie_key':access_key(None),'fetched_at':datetime(2020,1,1,tzinfo=timezone.utc),'rows':[row]}))
        store=self.store()
        self.assertEqual(store.snapshot()['rows'][0].region_key,NYC_REGION)
        self.assertEqual(store.last_complete,0)

    def test_single_active_refresh(self):
        store=self.store()
        gate=threading.Event()
        with patch.object(store,'_refresh',side_effect=lambda:gate.wait(2)):
            self.assertTrue(store.start())
            self.assertFalse(store.start(force=True))
            gate.set()
            store.future.result(2)

    def test_partial_failure_keeps_old_region_and_updates_other(self):
        store=self.store()
        old=listing()
        store.sources={'public:paris':{'fetched_at':1,'rows':[old]}}
        directory=f'<a href="{PARIS.url}">Paris</a><a href="{NYC.url}">New York City</a>'
        def client(): return httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=directory)))
        def fetch(region,first):
            if region.key=='paris': raise ValueError('Temporarily unavailable')
            return [listing(NYC,url='https://example.com/new')]
        with patch.object(store,'_client',side_effect=client),patch.object(store,'_fetch',side_effect=fetch):
            store.start();store.future.result(3)
        snapshot=store.snapshot()
        self.assertIn(old,snapshot['rows'])
        self.assertEqual(len(snapshot['rows']),2)
        self.assertIn('public:paris',snapshot['errors'])
        self.assertEqual(snapshot['cycle'],1)
        self.assertFalse(snapshot['refreshing'])

    def test_successful_empty_region_removes_old_listings(self):
        store=self.store()
        store.sources={'public:paris':{'fetched_at':1,'rows':[listing()]}}
        with patch.object(store,'_client',side_effect=lambda:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=f'<a href="{PARIS.url}">Paris</a>')))),patch.object(store,'_fetch',return_value=[]):
            store.start();store.future.result(3)
        self.assertEqual(store.snapshot()['rows'],[])
        self.assertGreater(store.last_complete,0)
        self.assertFalse(store.start())

    def test_discovery_failure_retains_all_cached_regions(self):
        store=self.store()
        store.regions={'paris':PARIS}
        store.sources={'public:paris':{'fetched_at':1,'rows':[listing()]}}
        with patch.object(store,'_client',side_effect=lambda:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='Changed markup')))):
            store.start();store.future.result(3)
        self.assertEqual(len(store.snapshot()['rows']),1)
        self.assertIn('paris',store.snapshot()['regions'])
        self.assertIn('public',store.snapshot()['errors'])


class FakeStore:
    def __init__(self):
        self.state=dict(rows=[listing(), listing(NYC,url='https://example.com/nyc',area='Greenpoint')],
                        regions={'paris':PARIS,NYC_REGION:NYC}, errors={}, refreshing=True,
                        completed=1,total=2,revision=1,cycle=0,updated_at=1,last_complete=0)
    def start(self,force=False): return False
    def snapshot(self): return dict(self.state)


UI_STORE=FakeStore()


class PageTests(unittest.TestCase):
    def setUp(self):
        global UI_STORE
        UI_STORE=FakeStore()
        self.temp=tempfile.TemporaryDirectory()
        source=(ROOT/'app.py').read_text()
        source=source.replace('Path(__file__).parent',f'Path({self.temp.name!r})')
        source=source.replace('store = _listings_store(auth_cookie)',"store = __import__('test_advanced_search').UI_STORE")
        source=source.replace('_prefetch_page_galleries(page_rows, auth_cookie)','pass # no network in UI tests')
        self.app=AppTest.from_string(source,default_timeout=10)

    def tearDown(self): self.temp.cleanup()

    def test_cached_page_renders_during_refresh_and_region_filter_works(self):
        app=self.app.run()
        self.assertEqual(list(app.exception),[])
        self.assertEqual(app.title[0].value,'ListingProject Advanced Search')
        self.assertIn('2 listings',app.main.subheader[-1].value)
        app.multiselect(key='region_keys').set_value(['paris']).run()
        self.assertEqual(list(app.exception),[])
        self.assertIn('1 listing',app.main.subheader[-1].value)
        self.assertFalse(any(c.label=='Brooklyn' for c in app.checkbox))
        app.multiselect(key='region_keys').set_value([NYC_REGION]).run()
        self.assertTrue(any(c.label=='Brooklyn' for c in app.checkbox))

    def test_background_addition_toast_once_preserves_filter(self):
        app=self.app.run()
        app.multiselect(key='region_keys').set_value(['paris']).run()
        UI_STORE.state['rows'].append(listing(url='https://example.com/new'))
        UI_STORE.state.update(revision=2,cycle=1,refreshing=False)
        app.run()
        self.assertEqual(list(app.exception),[])
        self.assertEqual(app.multiselect(key='region_keys').value,['paris'])
        self.assertEqual(len(app.toast),1)
        self.assertIn('1 new listing',app.toast[0].value)
        app.run()
        self.assertEqual(len(app.toast),0)

    def test_saved_nyc_area_survives_loading_without_cache(self):
        UI_STORE.state['rows'] = []
        (Path(self.temp.name)/'.listings_filters.json').write_text(json.dumps({
            'borough_keys':['brooklyn'], 'neighborhoods':['Greenpoint']}))
        app=self.app.run()
        self.assertEqual(list(app.exception),[])
        self.assertEqual(app.multiselect(key='region_keys').value,[NYC_REGION])
        saved=json.loads((Path(self.temp.name)/'.listings_filters.json').read_text())
        self.assertEqual(saved['neighborhoods'],[NYC_REGION+'::Greenpoint'])

    def test_refresh_retains_page_and_card_order(self):
        UI_STORE.state['rows']=[listing(url=f'https://example.com/{i}') for i in range(20)]
        app=self.app.run()
        app.session_state['results_page']=2
        app.run()
        UI_STORE.state['rows'].insert(0,listing(url='https://example.com/new'))
        UI_STORE.state.update(revision=2,cycle=1,refreshing=False)
        app.run()
        self.assertEqual(app.session_state['results_page'],2)
        self.assertGreater(app.session_state['_listing_order']['https://example.com/new'],
                           app.session_state['_listing_order']['https://example.com/19'])

    def test_only_visible_results_are_marked_seen(self):
        UI_STORE.state['rows']=[listing(url=f'https://example.com/{i}') for i in range(20)]
        self.app.run()
        seen=json.loads((Path(self.temp.name)/'.listings_seen.json').read_text())['seen_urls']
        self.assertEqual(len(seen),15)

    def test_cold_start_can_select_region_before_data_arrives(self):
        UI_STORE.state['rows']=[]
        app=self.app.run()
        app.multiselect(key='region_keys').set_value(['paris']).run()
        self.assertEqual(list(app.exception),[])
        UI_STORE.state['rows']=[listing()]
        UI_STORE.state.update(revision=2,cycle=1,refreshing=False)
        app.run()
        self.assertEqual(list(app.exception),[])
        self.assertIn('1 listing',app.main.subheader[-1].value)
        self.assertEqual(len(app.toast),0)


if __name__ == '__main__': unittest.main()
