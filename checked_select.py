"""Searchable multi-select menus that keep selected options in the list."""
from __future__ import annotations

import hashlib
from typing import Callable

import streamlit as st


def option_key(key: str, option: str) -> str:
    return f"{key}_option_{hashlib.sha256(option.encode()).hexdigest()[:16]}"


def checked_select(
    label: str,
    options: list[str],
    *,
    key: str,
    format_func: Callable[[str], str] = str,
    placeholder: str = "Select options",
    on_change: Callable[[], None] | None = None,
) -> list[str]:
    """Keep selection in a non-widget key so search and reruns cannot erase it."""
    selected = list(st.session_state.get(key, []))
    options = list(dict.fromkeys([*options, *selected]))
    st.session_state[key] = selected

    def toggle(option: str) -> None:
        values = set(st.session_state[key])
        if st.session_state[option_key(key, option)]:
            values.add(option)
        else:
            values.discard(option)
        st.session_state[key] = [value for value in options if value in values]
        if on_change:
            on_change()

    def clear() -> None:
        st.session_state[key] = []
        if on_change:
            on_change()

    selected_labels = [format_func(value) for value in selected]
    summary = ", ".join(selected_labels) if len(selected) <= 2 else f"{len(selected)} selected"
    with st.container(key=f"checked_select_{key}"):
        st.markdown(label)
        with st.popover(summary or placeholder, key=f"{key}_menu", width="stretch"):
            query = st.text_input(f"Search {label.lower()}", key=f"{key}_search",
                                  placeholder="Search…").strip().casefold()
            st.button("Clear selection", key=f"{key}_clear", on_click=clear,
                      disabled=not selected, type="tertiary")
            matches = [option for option in options if query in format_func(option).casefold()]
            with st.container(height=260, border=False):
                for option in matches:
                    widget_key = option_key(key, option)
                    st.session_state[widget_key] = option in selected
                    st.checkbox(format_func(option), key=widget_key,
                                on_change=toggle, args=(option,))
                if not matches:
                    st.caption("No matching options." if options else "No options available yet.")
    return list(st.session_state[key])
