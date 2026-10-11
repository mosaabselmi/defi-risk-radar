"""Bounded orthogonal graph geometry. Never changes graph relationships."""
from collections import defaultdict
from heapq import heappop, heappush
import math


def _face(a, b, infrastructure=False):
    if infrastructure:
        return 'left', 'left'
    return ('bottom', 'top') if a[1] >= b[1] else ('top', 'bottom')


def _port(position, size, face, rank, count):
    x, y = position
    w, h = size
    # Keep attachment points away from rounded corners. Incoming and outgoing
    # endpoints share the ranking, so even converging links have distinct ports.
    offset = (rank + 1) / (count + 1) - .5
    if face in ('top', 'bottom'):
        return round(x + offset*w*.78,10), round(y + (h/2 if face == 'top' else -h/2),10)
    return round(x - w/2,10), round(y + offset*h*.72,10)


def _stub(point, face):
    dx, dy = {'left':(-.22,0), 'top':(0,.22), 'bottom':(0,-.22)}[face]
    return round(point[0]+dx, 10), round(point[1]+dy, 10)


def _crosses(a, b, box):
    left, bottom, right, top = box
    if a[0] == b[0]:
        return left < a[0] < right and max(min(a[1],b[1]),bottom) < min(max(a[1],b[1]),top)
    return bottom < a[1] < top and max(min(a[0],b[0]),left) < min(max(a[0],b[0]),right)


def _shared_segment(a,b,c,d):
    """Positive collinear overlap, excluding an ordinary crossing or endpoint."""
    for axis in (0,1):
        other = 1-axis
        if a[axis] == b[axis] == c[axis] == d[axis]:
            return max(0,min(max(a[other],b[other]),max(c[other],d[other]))-
                       max(min(a[other],b[other]),min(c[other],d[other])))
    return 0


def orthogonal_routes(graph, positions, sizes):
    """Route on a finite visibility grid with obstacle and shared-lane costs.

    Work is bounded by the displayed nodes/ports; no network calls or scenario
    state. A failure is explicit instead of drawing a line through a label.
    """
    ends, faces = defaultdict(list), {}
    edges = list(graph.edges)
    for index, (u,v) in enumerate(edges):
        first, last = _face(positions[u], positions[v], v == 'ethereum')
        faces[index] = first, last
        ends[u,first].append((index,0,v))
        ends[v,last].append((index,1,u))
    ports = {}
    for (key,face), attached in ends.items():
        axis = 0 if face != 'left' else 1
        attached.sort(key=lambda item: (positions[item[2]][axis],item[0],item[1]))
        for rank,(index,side,_) in enumerate(attached):
            ports[index,side] = _port(positions[key],sizes[key],face,rank,len(attached))
    stubs = {key:_stub(point,faces[key[0]][key[1]]) for key,point in ports.items()}
    reserved_ports = [(point,stubs[key]) for key,point in ports.items()]
    boxes = [(x-sizes[k][0]/2-.07,y-sizes[k][1]/2-.07,
              x+sizes[k][0]/2+.07,y+sizes[k][1]/2+.07)
             for k,(x,y) in positions.items()]
    xs = {point[0] for point in stubs.values()}
    ys = {point[1] for point in stubs.values()}
    for left,bottom,right,top in boxes:
        xs.update((round(left-.12,10),round(right+.12,10)))
        ys.update((round(bottom-.12,10),round(top+.12,10)))
    # Several perimeter lanes let long infrastructure links remain separate.
    for i in range(1,5):
        xs.update((min(b[0] for b in boxes)-i*.22,max(b[2] for b in boxes)+i*.22))
    xs, ys = sorted(xs), sorted(ys)
    lookup_x, lookup_y = {x:i for i,x in enumerate(xs)}, {y:i for i,y in enumerate(ys)}
    nodes = {(i,j):(x,y) for i,x in enumerate(xs) for j,y in enumerate(ys)
             if not any(left < x < right and bottom < y < top for left,bottom,right,top in boxes)}
    neighbors = defaultdict(list)
    for node,a in nodes.items():
        i,j = node
        for other,axis in [((i+1,j),0),((i,j+1),1)]:
            if (other in nodes and not any(_crosses(a,nodes[other],box) for box in boxes)
                    and not any(_shared_segment(a,nodes[other],c,d)>1e-9 for c,d in reserved_ports)):
                distance = math.dist(a,nodes[other])
                neighbors[node].append((other,axis,distance))
                neighbors[other].append((node,axis,distance))
    occupied = defaultdict(int)
    routes = {}
    infrastructure_lane = 0
    for index,edge in enumerate(edges):
        start,end = stubs[index,0],stubs[index,1]
        if edge[1] == 'ethereum':
            infrastructure_lane += 1
            lane = min(box[0] for box in boxes)-.22*(infrastructure_lane+1)
            # Infrastructure is shared, but its modeled links remain separate
            # around the perimeter rather than cutting through asset branches.
            routes[edge] = [ports[index,0],start,(lane,start[1]),
                            (lane,end[1]),end,ports[index,1]]
            continue
        origin = (lookup_x[start[0]],lookup_y[start[1]])
        target = (lookup_x[end[0]],lookup_y[end[1]])
        initial_axis = 0 if faces[index][0] == 'left' else 1
        heap = [(0,0,origin,initial_axis)]
        costs, previous = {(origin,initial_axis):0}, {}
        final = None
        while heap:
            _, cost, node, direction = heappop(heap)
            if cost > costs.get((node,direction), math.inf):
                continue
            if node == target:
                final = node,direction
                break
            for other,axis,distance in neighbors[node]:
                segment = tuple(sorted((node,other)))
                candidate = cost+distance+(.3 if axis != direction else 0)+occupied[segment]*(1000+distance*6)
                state = other,axis
                if candidate < costs.get(state, math.inf):
                    costs[state] = candidate
                    previous[state] = node,direction
                    heuristic = abs(xs[other[0]]-end[0])+abs(ys[other[1]]-end[1])
                    heappush(heap,(candidate+heuristic,candidate,other,axis))
        if final is None:
            raise ValueError('No clear orthogonal route for displayed dependency')
        path = [final[0]]
        while final in previous:
            final = previous[final]
            path.append(final[0])
        path.reverse()
        for a,b in zip(path,path[1:]):
            occupied[tuple(sorted((a,b)))] += 1
        routes[edge] = [ports[index,0]]+[nodes[node] for node in path]+[ports[index,1]]
    return routes
