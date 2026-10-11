"""Theme-aware layered rendering of dynamic wallet graphs, separate from demo."""
import math
from html import escape

import plotly.graph_objects as go
from src.viz import get_palette, MODELED_EDGE_DASH


def wallet_layout(graph):
    assets = graph.graph['asset_order']
    columns = min(3, max(1, len(assets)))
    groups = max(1, math.ceil(len(assets) / columns))
    positions = {'position': (0, 2), 'pool': (0, 1),
                 'oracle': (0, -groups*6 + .8), 'ethereum': (0, -groups*6 - .5)}
    sizes = {key: (4, .65) for key in positions}
    for key, data in graph.nodes(data=True):
        if not data.get('asset'):
            continue
        i = assets.index(data['asset'])
        x, y = (i % columns - (columns-1)/2)*8, -(i//columns)*6 - (4-data['layer'])
        if key.startswith('a_token:'):
            x -= 2.5
        elif key.startswith('debt_token:'):
            x += 2.5
        positions[key] = (x, y)
        sizes[key] = (2.1, .7) if data['layer'] == 2 else (4, .8)
    return positions, sizes


def _boundary(position, size, toward):
    x, y = position
    dx, dy = toward[0]-x, toward[1]-y
    scale = min(size[0]/(2*abs(dx)) if dx else math.inf, size[1]/(2*abs(dy)) if dy else math.inf)
    return x+dx*scale, y+dy*scale


def _box(position, size):
    x, y = position
    width, height = size
    points = []
    radius = .10
    for cx, cy, start in [(x+width/2-radius,y+height/2-radius,0), (x-width/2+radius,y+height/2-radius,90),
                           (x-width/2+radius,y-height/2+radius,180), (x+width/2-radius,y-height/2+radius,270)]:
        for step in range(9):
            angle = math.radians(start+step*90/8)
            points.append((cx+radius*math.cos(angle),cy+radius*math.sin(angle)))
    return points+[points[0]]


def plot_wallet_graph(graph, focus=None, highlighted=(), theme='dark'):
    palette = get_palette(theme)
    positions, sizes = wallet_layout(graph)
    extent = max(abs(x)+sizes[key][0]/2 for key,(x,y) in positions.items()) + 1
    fig = go.Figure()
    for u, v, evidence in graph.edges(data=True):
        a, b = positions[u], positions[v]
        # Long links use perimeter lanes; adjacent layers meet box faces.
        if u == 'position' and v != 'pool':
            lane = -extent
            approach = b[1]+sizes[v][1]/2+.15
            route = [_boundary(a,sizes[u],(lane,a[1])), (lane,a[1]), (lane,approach),
                     (b[0],approach), (b[0],b[1]+sizes[v][1]/2)]
        elif v == 'oracle' or v.startswith('source:shared:'):
            start = (a[0],a[1]-sizes[u][1]/2)
            approach = b[1]+sizes[v][1]/2+.15
            route = [start,(start[0],start[1]-.12),(extent,start[1]-.12),
                     (extent,approach),(b[0],approach),(b[0],b[1]+sizes[v][1]/2)]
        elif v == 'ethereum':
            lane = -extent
            route = [_boundary(a,sizes[u],(lane,a[1])), (lane,a[1]), (lane,b[1]), _boundary(b,sizes[v],(lane,b[1]))]
        elif u.startswith('reserve:') and v.startswith('valuation:'):
            lane = a[0]+3.7
            route = [_boundary(a,sizes[u],(lane,a[1])),(lane,a[1]),(lane,b[1]),_boundary(b,sizes[v],(lane,b[1]))]
        elif u == 'pool' and v.startswith('reserve:'):
            lane = b[0]-3.7
            route = [_boundary(a,sizes[u],(lane,a[1])), (lane,a[1]), (lane,b[1]), _boundary(b,sizes[v],(lane,b[1]))]
        else:
            # Sideways-then-vertical routing avoids rounded corners and label crossings.
            start = (a[0],a[1]-sizes[u][1]/2)
            end = (b[0],b[1]+sizes[v][1]/2)
            middle = (start[1]+end[1])/2
            route = [start,(start[0],middle),(end[0],middle),end]
        level = evidence['evidence_level']
        color = palette['accent'] if level == 'verified' else palette['muted']
        dash = {'verified':'solid','modeled':MODELED_EDGE_DASH,'unknown':'dot'}[level]
        fig.add_trace(go.Scatter(x=[p[0] for p in route],y=[p[1] for p in route],mode='lines',
            line=dict(color=color,dash=dash,width=1.7),showlegend=False,
            meta=dict(source=u,target=v,evidence_level=level),
            hovertemplate=escape(evidence['relation'])+'<br>'+level.upper()+f" · block {evidence['block_number']:,}<extra></extra>"))
        end = route[-1]
        previous = next(p for p in reversed(route[:-1]) if p != end)
        amount = min(.5,.2/math.dist(end,previous))
        fig.add_annotation(x=end[0],y=end[1],ax=end[0]+amount*(previous[0]-end[0]),ay=end[1]+amount*(previous[1]-end[1]),
                           xref='x',yref='y',axref='x',ayref='y',text='',showarrow=True,arrowhead=2,
                           arrowcolor=color,arrowwidth=1.7,name=u+' → '+v)
    for key,data in graph.nodes(data=True):
        selected, affected = key == focus, key in highlighted
        unresolved = key.startswith('source:') and not data.get('contract_address')
        color = palette['amber'] if selected or affected else palette['border']
        fill = palette['monitor_bg'] if selected or affected else palette['surface']
        points = _box(positions[key],sizes[key])
        details = escape(data['label']).replace('\n','<br>')+'<br>'+escape(data['kind'])
        if data.get('contract_address'):
            address = data['contract_address']
            details += '<br>'+escape(address[:22])+'<br>'+escape(address[22:])
        if data.get('shared_assets'):
            details += '<br>Configured for: ' + ', '.join(
                escape(graph.nodes['exposure:' + asset]['label'].split('\n')[0])
                for asset in data['shared_assets'])
        if data.get('supplied_units'):
            details += '<br>Supply: '+escape(data['supplied_units'])+'<br>Debt: '+escape(data['debt_units'])
        fig.add_trace(go.Scatter(x=[p[0] for p in points],y=[p[1] for p in points],mode='lines',fill='toself',hoveron='fills',
            fillcolor=fill,line=dict(color=color,width=2.5 if selected else 1.4,
                                     dash='dot' if unresolved else 'solid'),showlegend=False,name=key,
            hovertemplate=details+'<extra></extra>'))
        x,y = positions[key]
        label = escape(data['label']).replace('\n','<br>').replace(' / ', '<br>')
        # Compact display name; the full underlying-token role remains in hover
        # and evidence. This keeps the larger labels within narrow token boxes.
        label = label.replace('underlying token', 'ERC-20').replace('debt token', 'debt')
        label = label.replace('Imported position', 'Imported<br>position').replace('Aave Price Oracle', 'Aave Price<br>Oracle')
        label = label.replace('enabled collateral', 'Collateral').replace('variable debt', 'Debt').replace('Aave valuation', 'Oracle value')
        if data.get('contract_address') and key.startswith('source:'):
            address = data['contract_address']
            label = label.replace(address[:8] + '…' + address[-4:], address[:6] + '…' + address[-4:])
        fig.add_annotation(x=x,y=y,text=label,showarrow=False,font=dict(color=palette['ink'],size=14),name=key)
    groups = max(1,math.ceil(len(graph.graph['asset_order'])/3))
    fig.update_layout(height=560+max(0,groups-1)*320,margin=dict(l=5,r=5,t=5,b=5),showlegend=False,
        paper_bgcolor=palette['surface'],plot_bgcolor=palette['surface'],dragmode=False,
        xaxis=dict(visible=False,fixedrange=True,range=[-extent-.3,extent+.3]),
        yaxis=dict(visible=False,fixedrange=True,range=[positions['ethereum'][1]-.6,2.6]),
        hoverlabel=dict(bgcolor=palette['surface'],bordercolor=palette['border'],font=dict(color=palette['ink'],size=14)))
    return fig
