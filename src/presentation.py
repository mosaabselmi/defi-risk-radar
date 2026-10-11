"""Native wrapping layouts for readable values at desktop browser zoom."""
import streamlit as st


def responsive_row(key, minimum_width):
    """CSS grid on an owned container key; no generated Streamlit selectors."""
    st.html(f'<style>.st-key-{key} {{display:grid;grid-template-columns:'
            f'repeat(auto-fit,minmax(min(100%,{minimum_width}px),1fr));gap:16px;}}</style>')
    return st.container(horizontal=True, wrap=True, gap='medium', key=key)


def metric_cards(count, *, key):
    """Balanced grids: four cards become 2×2, never an orphaned 3+1."""
    columns = 4 if count == 4 else 3 if count in (3, 6) else 2
    narrow = 2 if count % 2 == 0 else 1
    st.html(f'''<style>
      .st-key-{key}_shell {{container-type:inline-size;}}
      .st-key-{key} {{display:grid;grid-template-columns:repeat({columns},minmax(0,1fr));grid-auto-rows:1fr;gap:16px;align-items:stretch;}}
      .st-key-{key} > div {{height:100%;min-width:0;}}
      @container (max-width:1050px) {{.st-key-{key} {{grid-template-columns:repeat({narrow},minmax(0,1fr));}}}}
      @container (max-width:470px) {{.st-key-{key} {{grid-template-columns:1fr;}}}}
    </style>''')
    with st.container(key=key + '_shell'):
        row = st.container(horizontal=True, wrap=True, gap='medium', key=key)
    return [row.container(border=True, height='stretch') for _ in range(count)]


def theme_chart(figure, *, graph=False):
    """Native Plotly theme for chart text; paired node surfaces stay readable.

    Presentation-only copy: topology, geometry, plotted data and mathematics are
    unchanged. Explicit graph node colors convey categories, never verification.
    """
    import plotly.graph_objects as go
    result = go.Figure(figure)
    result.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                         font_color=None, hoverlabel_bgcolor='#202734',
                         hoverlabel_font_color='#F2F3F1')
    if graph:
        for trace in result.data:
            if trace.meta:
                verified = trace.meta.get('evidence_level') == 'verified' or trace.meta.get('verified') is True
                trace.line.color = '#607A8F' if verified else '#7A8C9E'
        for annotation in result.layout.annotations:
            if annotation.showarrow:
                annotation.arrowcolor = '#6C8193'
    else:
        for trace in result.data:
            if trace.mode == 'lines':
                trace.line.color = '#607A8F'
            elif trace.mode == 'markers':
                trace.marker.color = '#AD7D37'
                trace.marker.line.color = '#607A8F'
        for annotation in result.layout.annotations:
            annotation.font.color = None
            annotation.bgcolor = 'rgba(0,0,0,0)'
        for shape in result.layout.shapes:
            if shape.type == 'rect':
                shape.fillcolor = 'rgba(174,75,87,0.08)'
                shape.opacity = 1
            else:
                shape.line.color = '#B8505C' if shape.y0 == shape.y1 else '#AD7D37'
        result.update_xaxes(color=None, tickfont_color=None, title_font_color=None)
        result.update_yaxes(color=None, tickfont_color=None, title_font_color=None)
    return result
