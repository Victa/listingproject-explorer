import unittest
from streamlit.testing.v1 import AppTest
from checked_select import option_key

SOURCE = '''
import streamlit as st
from checked_select import checked_select
st.session_state.setdefault('places', ['a'])
options = {'a': 'Apartment', 'h': 'House', 's': 'Art studio'}
checked_select('Space type', list(options), key='places', format_func=options.get)
st.write(st.session_state.places)
'''

class CheckedSelectTests(unittest.TestCase):
    def test_selection_stays_visible_and_can_be_unchecked(self):
        app = AppTest.from_string(SOURCE).run()
        self.assertEqual([c.label for c in app.checkbox], ['Apartment', 'House', 'Art studio'])
        self.assertTrue(app.checkbox(key=option_key('places', 'a')).value)
        app.checkbox(key=option_key('places', 'h')).check().run()
        self.assertEqual([c.label for c in app.checkbox], ['Apartment', 'House', 'Art studio'])
        self.assertEqual(app.session_state['places'], ['a', 'h'])
        app.checkbox(key=option_key('places', 'a')).uncheck().run()
        self.assertEqual(app.session_state['places'], ['h'])

    def test_search_preserves_hidden_selection_and_clear(self):
        app = AppTest.from_string(SOURCE).run()
        app.text_input(key='places_search').set_value('HOUSE').run()
        self.assertEqual([c.label for c in app.checkbox], ['House'])
        app.checkbox(key=option_key('places', 'h')).check().run()
        self.assertEqual(app.session_state['places'], ['a', 'h'])
        app.text_input(key='places_search').set_value('nothing').run()
        self.assertEqual(app.caption[0].value, 'No matching options.')
        self.assertEqual(app.session_state['places'], ['a', 'h'])
        app.text_input(key='places_search').set_value('').run()
        self.assertTrue(app.checkbox(key=option_key('places', 'a')).value)
        app.button(key='places_clear').click().run()
        self.assertEqual(app.session_state['places'], [])
        self.assertFalse(any(c.value for c in app.checkbox))
        self.assertFalse(app.exception)
