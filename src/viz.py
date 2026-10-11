"""Consistent Plotly visualizations for the research dashboard."""
import math
from html import escape

import networkx as nx
import plotly.graph_objects as go

from src.risk import stress_position

MODELED_EDGE_DASH = '12px,8px'

PALETTES = {
    'dark': dict(ink='#F2F3F1', muted='#A8BAC8', accent='#BCCAD5',
                 border='#62778A', surface='#202734', red='#DA7D85', amber='#D4AD6C',
                 exit_bg='#39252D', exit_text='#F0B0B5', stay_bg='#19332C',
                 stay_border='#77B394', stay_text='#A5D3B9', monitor_bg='#352F24',
                 monitor_text='#E7C995', grid='rgba(168,186,200,.18)'),
    'light': dict(ink='#172330', muted='#52687A', accent='#405E75',
                  border='#62778A', surface='#F2F4F6', red='#A33E49', amber='#86611E',
                  exit_bg='#FBECEF', exit_text='#91313D', stay_bg='#EAF5EE',
                  stay_border='#3D7756', stay_text='#275B3E', monitor_bg='#FBF3E3',
                  monitor_text='#725014', grid='rgba(82,104,122,.18)'),
}


def get_palette(theme='dark'):
    """Explicit colors for Plotly/custom presentation; native widgets use Streamlit."""
    return PALETTES.get(theme, PALETTES['dark']).copy()

LAYOUT = {
    'Position': (0, 3),
    'WETH': (-2.3, 2),
    'Aave Pool': (0, 2),
    'USDC': (2.3, 2),
    'Aave Price Oracle': (0, 1),
    'WETH Feed': (-1.2, 0),
    'USDC Feed': (1.2, 0),
    'Ethereum': (0, -1.1),
}
NODE_SIZES = {node: (1.6, .56) for node in LAYOUT}
NODE_SIZES.update({'WETH': (1.2, .56), 'USDC': (1.2, .56),
                   'Aave Price Oracle': (1.8, .70), 'Ethereum': (2, .72)})


def node_boundary(node, toward):
    """Clip a ray to the rounded boundary, including the curved corner regions."""
    x, y = LAYOUT[node]
    width, height = NODE_SIZES[node]
    dx, dy = toward[0] - x, toward[1] - y
    scale = min(width / (2 * abs(dx)) if dx else math.inf,
                height / (2 * abs(dy)) if dy else math.inf)
    rx, ry = .09, .09
    def inside(amount):
        px, py = abs(amount * dx), abs(amount * dy)
        if px > width/2 or py > height/2:
            return False
        if px <= width/2-rx or py <= height/2-ry:
            return True
        return ((px - (width/2-rx))/rx)**2 + ((py - (height/2-ry))/ry)**2 <= 1
    if not inside(scale):
        low, high = 0, scale
        for _ in range(40):
            middle = (low + high)/2
            if inside(middle):
                low = middle
            else:
                high = middle
        scale = low
    return x + scale * dx, y + scale * dy


def edge_route(source, target):
    """Main hierarchy descends; separate perimeter lanes reach shared infrastructure."""
    if (source, target) == ('Aave Pool', 'Ethereum'):
        return [node_boundary(source, (-1, 2)), (-1, 2), (-1, 1.5),
                (-2.6, 1.5), (-2.6, -.45), (-.5, -.45),
                (-.5, LAYOUT[target][1] + NODE_SIZES[target][1]/2)]
    if source in ('WETH', 'USDC') and target == 'Ethereum':
        lane = -3.1 if source == 'WETH' else 3.1
        return [node_boundary(source, (lane, 2)), (lane, 2), (lane, -1.1),
                node_boundary(target, (lane, -1.1))]
    return [node_boundary(source, LAYOUT[target]), node_boundary(target, LAYOUT[source])]


def rounded_node(node):
    """Rounded rectangle polygon in plot coordinates, including a full-box hover area."""
    x, y = LAYOUT[node]
    width, height = NODE_SIZES[node]
    rx, ry = .09, .09
    points = []
    for cx, cy, start in [(x + width/2-rx, y + height/2-ry, 0),
                           (x - width/2+rx, y + height/2-ry, 90),
                           (x - width/2+rx, y - height/2+ry, 180),
                           (x + width/2-rx, y - height/2+ry, 270)]:
        for step in range(9):
            angle = math.radians(start + step * 90/8)
            points.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
    return points + [points[0]]


def plot_graph(g, failed, theme='dark'):
    palette = get_palette(theme)
    affected = nx.ancestors(g, failed) if failed in g else set()
    fig = go.Figure()
    for u, v, meta in g.edges(data=True):
        route = edge_route(u, v)
        verified = meta.get('verified_onchain', False)
        color = palette['accent'] if verified else palette['border']
        evidence = (f"Verified source mapping · block {meta['block_number']:,}"
                    if verified else 'Modeled dependency')
        fig.add_trace(go.Scatter(
            x=[point[0] for point in route], y=[point[1] for point in route],
            mode='lines', line=dict(color=color, width=2 if verified else 1.5,
                                    dash='solid' if verified else MODELED_EDGE_DASH),
            name=f'{u} → {v}', meta=dict(source=u, target=v, verified=verified),
            hovertemplate=f'<b>{escape(u)} → {escape(v)}</b><br>{evidence}<br>'
                          + escape(meta.get('relation', '')) + '<extra></extra>',
            showlegend=False,
        ))
        # A short arrowhead on the final routed segment points into the target boundary.
        end, previous = route[-1], route[-2]
        tail = min(.5, .22 / math.dist(end, previous))
        fig.add_annotation(
            x=end[0], y=end[1], ax=end[0] + tail*(previous[0]-end[0]),
            ay=end[1] + tail*(previous[1]-end[1]),
            xref='x', yref='y', axref='x', ayref='y',
            name=f'{u} → {v}', text='', showarrow=True, arrowhead=2, arrowsize=1.1,
            arrowwidth=2 if verified else 1.5, arrowcolor=color, standoff=0,
        )
    for node in g.nodes:
        x, y = LAYOUT[node]
        color = (palette['red'] if node == failed else palette['amber'] if node in affected
                 else palette['accent'] if node == 'Position' else palette['border'])
        fill = (palette['exit_bg'] if node == failed else palette['monitor_bg']
                if node in affected else palette['surface'])
        addr = g.nodes[node].get('contract_address')
        hover = f'<b>{escape(node)}</b><br>{escape(g.nodes[node]["kind"])}'
        if addr:
            # Split the address visually, preserving every character for inspection.
            hover += f'<br>Contract address:<br>{addr[:22]}<br>{addr[22:]}'
        verified = [meta for _, _, meta in g.in_edges(node, data=True)
                    if meta.get('verified_onchain')]
        if verified:
            hover += f"<br>Configured source verified at block {verified[0]['block_number']:,}"
        hover += '<br><i>Only evidenced oracle-to-source edges are verified;<br>other edges are modeled.</i>'
        points = rounded_node(node)
        fig.add_trace(go.Scatter(
            x=[point[0] for point in points], y=[point[1] for point in points],
            mode='lines', fill='toself', fillcolor=fill, hoveron='fills', name=node,
            line=dict(color=color, width=2 if node == failed or node in affected else 1.5),
            hovertemplate=hover + '<extra></extra>', showlegend=False,
        ))
        label = ('Aave Price<br>Oracle' if node == 'Aave Price Oracle' else
                 'Ethereum<br>Infrastructure' if node == 'Ethereum' else escape(node))
        fig.add_annotation(x=x, y=y, text=label, showarrow=False,
                           font=dict(size=14, color=palette['ink'], family='Arial'))
    fig.update_layout(
        height=310, paper_bgcolor=palette['surface'], plot_bgcolor=palette['surface'],
        xaxis=dict(visible=False, range=[-3.35, 3.35], fixedrange=True),
        yaxis=dict(visible=False, range=[-1.58, 3.42], fixedrange=True),
        margin=dict(l=4, r=4, t=4, b=4), showlegend=False,
        dragmode=False, hoverlabel=dict(bgcolor=palette['surface'], bordercolor=palette['border'],
                                       font=dict(color=palette['ink'], size=13)),
    )
    return fig


def plot_health_curve(position, selected_shock, asset='WETH', selected_is_scenario=True, theme='dark'):
    palette = get_palette(theme)
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
        focus_hf = [position.health_factor]
        fig.add_trace(go.Scatter(
            x=shocks, y=health, mode='lines', name='Health factor',
            line=dict(color=palette['accent'], width=3), fill='tozeroy',
            fillcolor='rgba(168,186,200,0.08)',
            hovertemplate=asset + ' market shock: %{x}%<br>HF: %{y:.2f}<extra></extra>',
        ))
        fig.add_hline(y=1, line_color=palette['red'], line_dash='dot',
                      annotation_text='HF = 1 · liquidation threshold',
                      annotation_font_color=palette['red'],
                      annotation_position='top left')
        fig.add_hrect(y0=0, y1=1, fillcolor=palette['exit_bg'], opacity=.55,
                      line_width=0, layer='below')
        if selected_is_scenario:
            selected = stressed(selected_shock).health_factor
            focus_hf.append(selected)
            fig.add_trace(go.Scatter(x=[selected_shock], y=[selected],
                mode='markers', marker=dict(size=11, color=palette['amber'], line=dict(color=palette['surface'],width=2)),
                name='Selected shock', hovertemplate='Selected HF: %{y:.2f}<extra></extra>'))
            fig.add_vline(x=selected_shock, line_color=palette['amber'],
                          line_dash='dash', line_width=1)
            fig.add_annotation(x=.5, y=1.08, xref='paper', yref='paper',
                               text=f'Selected {asset} shock: {selected_shock:+g}% · HF {selected:.2f}',
                               showarrow=False, xanchor='center', yanchor='bottom',
                               bgcolor=palette['surface'], bordercolor=palette['amber'], borderpad=4,
                               font=dict(color=palette['ink'], size=13))
        # Presentation zoom only; every sampled HF and the selected point remain unchanged.
        fig.update_yaxes(range=[0, max(2, max(focus_hf)*1.3)])
    else:
        fig.add_annotation(text='No debt: health factor is not finite',
                           showarrow=False, font=dict(color=palette['muted'], size=14))
    fig.update_layout(
        height=320, margin=dict(l=72, r=18, t=52, b=72),
        paper_bgcolor=palette['surface'], plot_bgcolor=palette['surface'],
        font=dict(color=palette['ink'], size=12),
        hoverlabel=dict(bgcolor=palette['surface'], font_color=palette['ink']),
        xaxis=dict(title=dict(text=asset + ' market-price shock (%)', standoff=14),
                   automargin=True, tickmode='array', tickvals=[-100, -50, 0, 50, 100],
                   ticks='outside', ticklen=5, range=[-100, 105], showgrid=True,
                   gridcolor=palette['grid'], zeroline=False),
        yaxis=dict(title=dict(text='Health factor', standoff=12), automargin=True,
                   ticks='outside', ticklen=5, nticks=5, showgrid=True,
                   gridcolor=palette['grid'], zeroline=False),
        showlegend=False,
    )
    return fig
