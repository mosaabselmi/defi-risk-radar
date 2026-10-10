"""Consistent Plotly visualizations for the research dashboard."""
import math

import networkx as nx
import plotly.graph_objects as go

from src.risk import stress_position

INK = '#F2F3F1'
MUTED = '#A8BAC8'
ACCENT = '#BCCAD5'
RED = '#DA7D85'
AMBER = '#D4AD6C'
SLATE = '#8C9FB0'

LAYOUT = {
    'Position': (0.50, 0.94),
    'Aave Pool': (0.50, 0.72),
    'WETH': (0.17, 0.72),
    'USDC': (0.83, 0.72),
    'Aave Price Oracle': (0.50, 0.45),
    'WETH Feed': (0.27, 0.20),
    'USDC Feed': (0.73, 0.20),
    'Ethereum': (0.50, 0.035),
}


def plot_graph(g, failed):
    affected = nx.ancestors(g, failed) if failed in g else set()
    fig = go.Figure()
    for u, v in g.edges():
        x0, y0 = LAYOUT[u]
        x1, y1 = LAYOUT[v]
        dx, dy = x1-x0, y1-y0
        length = (dx*dx+dy*dy)**0.5
        if not length:
            continue
        # Arrowheads stop before the target marker so direction remains visible.
        source_offset, target_offset = 0.06, 0.073
        x_start = x0 + dx * source_offset / length
        y_start = y0 + dy * source_offset / length
        x_end = x1 - dx * target_offset / length
        y_end = y1 - dy * target_offset / length
        fig.add_annotation(
            x=x_end, y=y_end, ax=x_start, ay=y_start,
            xref='x', yref='y', axref='x', ayref='y',
            text='', showarrow=True, arrowhead=2, arrowsize=1,
            arrowwidth=1.8, arrowcolor='#8C9FB0',
        )
    for node in g.nodes:
        x, y = LAYOUT[node]
        color = RED if node == failed else AMBER if node in affected else ACCENT if node == 'Position' else SLATE
        addr = g.nodes[node].get('contract_address')
        hover = f'<b>{node}</b><br>{g.nodes[node]["kind"]}'
        if addr:
            # Split the address visually, preserving every character for inspection.
            hover += f'<br>Contract address:<br>{addr[:22]}<br>{addr[22:]}'
        verified = [meta for _, _, meta in g.in_edges(node, data=True)
                    if meta.get('verified_onchain')]
        if verified:
            hover += f"<br>Configured source verified at block {verified[0]['block_number']:,}"
        hover += '<br><i>Only evidenced oracle-to-source edges are verified;<br>other edges are modeled.</i>'
        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode='markers+text', text=[node],
            textposition='bottom center',
            marker=dict(size=28 if node == 'Position' else 23, color=color,
                        line=dict(color='#202734', width=3)),
            textfont=dict(size=14, color=INK, family='Arial'),
            hovertemplate=hover + '<extra></extra>', showlegend=False,
        ))
    fig.update_layout(
        height=355, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(visible=False, range=[-0.08, 1.08], fixedrange=True),
        yaxis=dict(visible=False, range=[-0.13, 1.08], fixedrange=True),
        margin=dict(l=10, r=10, t=12, b=5), showlegend=False,
        dragmode=False, hoverlabel=dict(bgcolor='#202734', bordercolor='#8C9FB0',
                                       font=dict(color=INK, size=13)),
    )
    return fig


def plot_health_curve(position, selected_shock, asset='WETH', selected_is_scenario=True):
    shocks = list(range(-95, 101, 5))
    def stressed(shock):
        return stress_position(position, shock if asset == 'WETH' else 0,
                               shock if asset == 'USDC' else 0)
    health = []
    for shock in shocks:
        try:
            health.append(stressed(shock).health_factor)
        except ValueError:
            health.append(None)  # Do not fabricate values below the supported price floor.
    fig = go.Figure()
    if any(h is not None and math.isfinite(h) for h in health):
        fig.add_trace(go.Scatter(
            x=shocks, y=health, mode='lines', name='Health factor',
            line=dict(color=ACCENT, width=3), fill='tozeroy',
            fillcolor='rgba(168,186,200,0.08)',
            hovertemplate=asset + ' market shock: %{x}%<br>HF: %{y:.2f}<extra></extra>',
        ))
        fig.add_hline(y=1, line_color=RED, line_dash='dot',
                      annotation_text='HF = 1', annotation_font_color=RED,
                      annotation_position='top left')
        if selected_is_scenario:
            selected = stressed(selected_shock).health_factor
            fig.add_trace(go.Scatter(x=[selected_shock], y=[selected],
                mode='markers', marker=dict(size=11, color=AMBER, line=dict(color='#202734',width=2)),
                name='Selected shock', hovertemplate='Selected HF: %{y:.2f}<extra></extra>'))
    else:
        fig.add_annotation(text='No debt: health factor is not finite',
                           showarrow=False, font=dict(color=MUTED, size=14))
    fig.update_layout(
        height=265, margin=dict(l=5, r=10, t=5, b=20),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color=INK, size=12),
        xaxis=dict(title=asset + ' market-price shock (%)', showgrid=True,
                   gridcolor='rgba(168,186,200,.18)', zeroline=False),
        yaxis=dict(title='Health factor', showgrid=True,
                   gridcolor='rgba(168,186,200,.18)', zeroline=False),
        showlegend=False,
    )
    return fig
