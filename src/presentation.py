"""Native wrapping layouts for readable values at desktop browser zoom."""
import streamlit as st


def responsive_row(key, minimum_width):
    """CSS grid on an owned container key; no generated Streamlit selectors."""
    st.html(f'<style>.st-key-{key} {{display:grid;grid-template-columns:'
            f'repeat(auto-fit,minmax(min(100%,{minimum_width}px),1fr));gap:16px;}}</style>')
    return st.container(horizontal=True, wrap=True, gap='medium', key=key)


def metric_cards(count, *, key):
    """Content-height cards wrap instead of squeezing large values into columns."""
    row = responsive_row(key, 260)
    return [row.container() for _ in range(count)]
