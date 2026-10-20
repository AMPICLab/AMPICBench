#!/usr/bin/env python3
"""
ams_symbols.py - the AMS Visio stencil (AMS.vss) as Schemdraw elements.

The stencil was converted once with libvisio (vss2xhtml, apt package libvisio-tools) into one SVG per master
in tools/ams_symbols/ (see `python3 ams_symbols.py --import AMS.vss`). This module turns those SVGs into
Schemdraw elements with the same geometry and stroke weights as the Visio shapes:

    from ams_symbols import AMS, ams_config
    ams_config()                         # Schemdraw scale so 1 Visio point = 1 output point (lw 2.5 like Visio)
    d += AMS.NMOS().anchor('drain').at((0, 0))           # anchors: drain, gate, source, center
    d += AMS.PMOS().reverse()                            # gate on the right -> left (mirror)
    d += AMS.Resistor().right().length(3)               # two-terminal parts stretch like elm.Resistor
    d += AMS.Capacitor(); AMS.Ground(); AMS.VDD()

Stencil geometry is on a 9 pt grid (NMOS: drain-source 36 pt, gate lead 27 pt left of the drain/source rail).
Schemdraw coordinates use SCALE units per point, chosen so a MOSFET is 1.5 units tall like elm.NFet.
"""
import os, re, sys, math, glob, subprocess
import schemdraw
from schemdraw.elements import Element, Element2Term
from schemdraw.segments import Segment, SegmentPoly, SegmentText

HERE = os.path.dirname(os.path.abspath(__file__))
SYMDIR = os.path.join(HERE, 'ams_symbols')
SCALE = 1.5 / 36            # schemdraw units per Visio point
PT_PER_UNIT = 1 / SCALE     # output points per schemdraw unit when 1 Visio pt = 1 output pt


def ams_config(fontsize=10):
    """Schemdraw global scale matching the stencil: strokes 2.5 pt (wires) and 3.5 pt (gates, plates)."""
    schemdraw.config(inches_per_unit=PT_PER_UNIT / 72, lw=2.5, fontsize=fontsize)


# ----------------------------------------------------------------------------- SVG path parsing
def _arc(p0, rx, ry, phi, large, sweep, p1, n=24):
    """SVG elliptical arc (endpoint form) -> list of points (rx = ry in this stencil)."""
    x1, y1 = p0; x2, y2 = p1
    if rx == 0 or ry == 0:
        return [p1]
    ph = math.radians(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = math.cos(ph) * dx + math.sin(ph) * dy
    y1p = -math.sin(ph) * dx + math.cos(ph) * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = math.cos(ph) * cxp - math.sin(ph) * cyp + (x1 + x2) / 2
    cy = math.sin(ph) * cxp + math.cos(ph) * cyp + (y1 + y2) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    pts = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        x = math.cos(ph) * rx * math.cos(t) - math.sin(ph) * ry * math.sin(t) + cx
        y = math.sin(ph) * rx * math.cos(t) + math.cos(ph) * ry * math.sin(t) + cy
        pts.append((x, y))
    return pts


def _subpaths(d):
    """path data -> list of (points, closed)"""
    tok = re.findall(r'[MLAZCmlazc]|-?\d*\.?\d+(?:e-?\d+)?', d)
    out, cur, closed, i, pos = [], [], False, 0, (0, 0)
    num = lambda k: float(tok[k])
    cmd = None
    while i < len(tok):
        t = tok[i]
        if t in 'MLAZC':
            cmd = t; i += 1
            if t == 'Z':
                if cur:
                    out.append((cur, True)); cur = []
                continue
        if cmd == 'M':
            if cur:
                out.append((cur, False))
            pos = (num(i), num(i + 1)); cur = [pos]; i += 2; cmd = 'L'
        elif cmd == 'L':
            pos = (num(i), num(i + 1)); cur.append(pos); i += 2
        elif cmd == 'A':
            rx, ry, phi, la, sw, x, y = (num(i + k) for k in range(7)); i += 7
            cur += _arc(pos, rx, ry, phi, int(la), int(sw), (x, y)); pos = (x, y)
        elif cmd == 'C':                 # cubic Bezier, sampled
            c1, c2, p = (num(i), num(i + 1)), (num(i + 2), num(i + 3)), (num(i + 4), num(i + 5)); i += 6
            p0 = pos
            for k in range(1, 13):
                s = k / 12
                cur.append(tuple((1 - s) ** 3 * p0[j] + 3 * (1 - s) ** 2 * s * c1[j] + 3 * (1 - s) * s ** 2 * c2[j] + s ** 3 * p[j] for j in (0, 1)))
            pos = p
        else:
            i += 1
    if cur:
        out.append((cur, False))
    return out


def load_svg(name):
    """-> list of drawing primitives in Visio points: ('line', pts, lw, cap), ('fill', pts, color), ('text', xy, s, size)"""
    s = open(os.path.join(SYMDIR, name + '.svg')).read()
    prims = []
    for m in re.finditer(r'<path d="([^"]*)"\s*style="([^"]*)"', s, re.S):
        d, st = m.group(1), m.group(2)
        if 'stroke-opacity: 0.0000' in st:
            continue                      # invisible shape frame
        sw = re.search(r'stroke-width: ([\d.]+)', st)
        fill = re.search(r'fill: (#[0-9a-fA-F]{6}|none)', st)
        stroke = re.search(r'stroke: (#[0-9a-fA-F]{6})', st)
        for pts, closed in _subpaths(d):
            if fill and fill.group(1) != 'none' and not stroke:
                prims.append(('fill', pts, fill.group(1)))
            elif stroke:
                prims.append(('line', pts + ([pts[0]] if closed else []), float(sw.group(1)) if sw else 1.0,
                              'butt' if 'linecap: butt' in st else 'round'))
    for m in re.finditer(r'<text x="([\d.-]+)" y="([\d.-]+)">\s*<tspan[^>]*font-size="([\d.]+)"[^>]*>([^<]*)</tspan>', s):
        prims.append(('text', (float(m.group(1)), float(m.group(2))), m.group(4).strip(), float(m.group(3))))
    return prims


def _segments(prims, ref, mirror_y=True, stroke=None):
    """Visio points -> schemdraw segments relative to ref (y flipped). stroke: one line weight for all lines
    (the analog drawings use 1.5 pt everywhere; the stencil masters carry 2.5 / 3.5 pt)."""
    X = lambda p: ((p[0] - ref[0]) * SCALE, -(p[1] - ref[1]) * SCALE)
    segs = []
    for pr in prims:
        if pr[0] == 'line':
            segs.append(Segment([X(p) for p in pr[1]], lw=stroke or pr[2], capstyle='butt' if pr[3] == 'butt' else 'round',
                                joinstyle='miter' if pr[3] == 'butt' else 'round'))
        elif pr[0] == 'fill':
            segs.append(SegmentPoly([X(p) for p in pr[1]], fill=pr[2], color=pr[2], lw=(stroke or 1.0) * 0.5, closed=True))
        elif pr[0] == 'text':
            x, y = X(pr[1])
            segs.append(SegmentText((x, y), pr[2], fontsize=pr[3], align=('left', 'bottom')))
    return segs


# ----------------------------------------------------------------------------- symbol definitions
# terminals in Visio points (from the stencil SVGs); ref = point placed at the element origin
SYMS = {
    'NMOS':  dict(ref='drain', anchors=dict(drain=(117, 18), source=(117, 54), gate=(90, 36))),
    'NMOS1': dict(ref='drain', anchors=dict(drain=(117, 18), source=(117, 54), gate=(90, 36))),
    'NMOS2': dict(ref='drain', anchors=dict(drain=(117, 18), source=(117, 54), gate=(90, 36))),
    'NMOS2A': dict(master='NMOS2', ref='drain', anchors=dict(drain=(288, 360), source=(288, 396), gate=(261, 378))),
    'PMOS2A': dict(master='PMOS2', ref='source', anchors=dict(source=(252, 378), drain=(252, 414), gate=(225, 396))),
    'PMOS':  dict(ref='source', anchors=dict(source=(252, 378), drain=(252, 414), gate=(219.9375, 396))),
    'PMOS1': dict(ref='source', anchors=dict(source=(252, 378), drain=(252, 414), gate=(219.9375, 396))),
    'Ground': dict(ref='start', anchors=dict(start=(288, 387))),
    'VDD':    dict(ref='start', anchors=dict(start=(288, 370.8))),
}
TWO_TERM = {'Resistor': ((288, 396), (324, 396)), 'Capacitor': ((288, 396), (324, 396)),
            'Bottom_Plate_Capacitor': ((288, 396), (324, 396))}


def _fet_offsets(name):
    """anchor offsets of PMOS/NMOS variants are read from the SVG (gate lead end, rail ends)"""
    return SYMS[name]


class _Sym(Element):
    _name = None

    def __init__(self, stroke=None, **kw):
        super().__init__(**kw)
        spec = SYMS[self._name]
        ref = spec['anchors'][spec['ref']]
        self.segments.extend(_segments(load_svg(spec.get('master', self._name)), ref, stroke=stroke))
        for k, p in spec['anchors'].items():
            self.anchors[k] = ((p[0] - ref[0]) * SCALE, -(p[1] - ref[1]) * SCALE)
        if 'drain' in spec['anchors']:
            dr, so = self.anchors['drain'], self.anchors['source']
            self.anchors['center'] = (dr[0], (dr[1] + so[1]) / 2)
        self.params['theta'] = 0          # the stencil is drawn upright; do not follow the drawing direction
        self.params['drop'] = self.anchors.get('source' if self._name.startswith('NMOS') else 'drain', (0, 0))


class _Sym2(Element2Term):
    _name = None

    def __init__(self, stroke=None, **kw):
        super().__init__(**kw)
        a, b = TWO_TERM[self._name]
        L = (b[0] - a[0]) * SCALE
        # Element2Term grows the leads on segments[0]: give it the terminal-to-terminal path (with a break,
        # so nothing is drawn across the body); the stencil's own geometry follows
        nan = float('nan')
        self.segments.append(Segment([(0, 0), (nan, nan), (L, 0)], lw=stroke or 2.5, capstyle='round'))
        self.segments.extend(_segments(load_svg(self._name), a, stroke=stroke))


class GndTri(Element):
    """open-triangle ground of the analog figures: 10.8 pt stem, 14.4 pt wide, 10.8 pt tall (origin at the top)"""
    def __init__(self, stroke=1.5, **kw):
        super().__init__(**kw)
        k = SCALE
        self.segments.append(Segment([(0, 0), (0, -10.8 * k)], lw=stroke))
        self.segments.append(Segment([(-7.2 * k, -10.8 * k), (7.2 * k, -10.8 * k), (0, -21.6 * k), (-7.2 * k, -10.8 * k)],
                                     lw=stroke, joinstyle='miter'))
        self.anchors['start'] = (0, 0)
        self.params['theta'] = 0
        self.params['drop'] = (0, 0)


class VddTee(Element):
    """VDD tee of the analog figures: 10.8 pt stem, 18 pt bar, 'VDD' in bold italic serif above (origin at the bottom)"""
    def __init__(self, stroke=1.5, text=None, fontsize=10, **kw):
        super().__init__(**kw)
        k = SCALE
        self.segments.append(Segment([(0, 0), (0, 10.8 * k)], lw=stroke))
        self.segments.append(Segment([(-9 * k, 10.8 * k), (9 * k, 10.8 * k)], lw=stroke))
        if text:
            self.segments.append(SegmentText((0, 12.5 * k), text, fontsize=fontsize, align=('center', 'bottom')))
        self.anchors['start'] = (0, 0)
        self.params['theta'] = 0
        self.params['drop'] = (0, 0)


class _NS:
    pass


AMS = _NS()
for _n in SYMS:
    setattr(AMS, _n, type(_n, (_Sym,), {'_name': _n}))
for _n in TWO_TERM:
    setattr(AMS, _n, type(_n, (_Sym2,), {'_name': _n}))
AMS.GndTri, AMS.VddTee = GndTri, VddTee


# ----------------------------------------------------------------------------- import from the .vss
def import_vss(vss, outdir=SYMDIR):
    """Convert a Visio stencil to one cropped SVG per master (needs vss2xhtml / vss2raw from libvisio-tools)."""
    raw = subprocess.run(['vss2raw', vss], capture_output=True, text=True).stdout
    names = []
    for i, l in enumerate(x for x in raw.splitlines() if 'startPage' in x):
        m = re.search(r'draw:name: ([^,)]*)', l)
        names.append(m.group(1).strip() if m else f'page{i}')
    x = subprocess.run(['vss2xhtml', vss], capture_output=True, text=True).stdout
    svgs = re.findall(r'<svg:svg.*?</svg:svg>', x, re.S)
    assert len(svgs) == len(names), (len(svgs), len(names))
    os.makedirs(outdir, exist_ok=True)
    for n, s in zip(names, svgs):
        s = s.replace('svg:', '')
        s = re.sub(r'<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', s, count=1)
        pts = [(float(a), float(b)) for a, b in re.findall(r'[ML]\s*(-?[\d.]+),(-?[\d.]+)', s)]
        pts += [(float(a), float(b)) for a, b in re.findall(r'<text x="([\d.-]+)" y="([\d.-]+)"', s)]
        if pts:
            x0, x1 = min(p[0] for p in pts) - 3, max(p[0] for p in pts) + 3
            y0, y1 = min(p[1] for p in pts) - 3, max(p[1] for p in pts) + 3
            s = re.sub(r'width="[^"]*" height="[^"]*" viewBox="[^"]*"',
                       f'width="{x1 - x0:.2f}pt" height="{y1 - y0:.2f}pt" viewBox="{x0} {y0} {x1 - x0} {y1 - y0}"', s, count=1)
        open(os.path.join(outdir, re.sub(r'[^A-Za-z0-9]+', '_', n).strip('_') + '.svg'), 'w').write(s)
    return names


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--import':
        print(f'{len(import_vss(sys.argv[2]))} symbols written to {SYMDIR}')
    else:
        print(__doc__)
