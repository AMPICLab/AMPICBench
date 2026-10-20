#!/usr/bin/env python3
"""
analog_sheet.py - draw transistor-level schematics in the style of the user's Visio figures
(Two_Stage_Amplfiier.vsd, Telescopic_Stage.vsd, Folded_Cascode_Class_AB_Opamps_v1.vsd):

  * analog MOSFET symbols with a source arrow (AMS stencil masters NMOS2 / PMOS2), bulk not drawn
  * one weight for every line, 1.5 pt
  * every PMOS source on its own VDD tee, every NMOS source on its own open-triangle ground (no supply rails)
  * device names and sizes in plain Arial 9 black, net names dark gray, currents light gray (same size),
    'VDD' in bold italic Times 10
  * coordinates in Visio points, y pointing down, on a 9 pt grid (a MOSFET is 36 pt from drain to source and
    its gate lead ends 27 pt from the source/drain rail)

    s = Sheet()
    m = s.nmos(x, y_drain, gate='left', name='M1', sub='4 x 2.25/0.5')   # -> dict of terminal points
    s.wire((x0, y0), (x1, y1), ...); s.dot((x, y)); s.gnd((x, y)); s.vdd((x, y))
    s.res((x0, y0), (x1, y1), 'Rz'); s.cap(...); s.text((x, y), 'vout', kind='net')
    s.save('out.svg')   # .svg / .pdf / .png
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import schemdraw
import schemdraw.elements as elm
from ams_symbols import AMS, SCALE, PT_PER_UNIT

LW = 1.5
SANS, SERIF = 'Liberation Sans', 'Liberation Serif'      # metric-compatible with Arial / Times New Roman
BLUE = '#0000ff'
GRAY = '#505050'          # net names: dark gray
LIGHT = '#a0a0a0'         # operating-point currents: light gray
STYLES = {
    'device': dict(color='black', fontsize=9, family=SANS),          # device names: plain, same size as sizes
    'node':   dict(color=GRAY, fontsize=9, family=SANS),             # net names in gray
    'net':    dict(color=GRAY, fontsize=9, family=SANS),
    'size':   dict(color='black', fontsize=9, family=SANS),
    'op':     dict(color=LIGHT, fontsize=9, family=SANS),
    'vdd':    dict(color='black', fontweight='bold', fontstyle='italic', fontsize=10, family=SERIF),
    'title':  dict(color='black', fontsize=12, family=SANS),
}


class Sheet:
    def __init__(self):
        schemdraw.config(inches_per_unit=PT_PER_UNIT / 72, lw=LW, fontsize=12, font=SANS)
        self.d = schemdraw.Drawing(show=False)
        self.texts = []

    @staticmethod
    def U(p):
        return (p[0] * SCALE, -p[1] * SCALE)

    @staticmethod
    def P(u):
        return (u[0] / SCALE, -u[1] / SCALE)

    # ------------------------------------------------------------------ primitives
    def wire(self, *pts):
        for a, b in zip(pts[:-1], pts[1:]):
            self.d.add(elm.Line().at(self.U(a)).to(self.U(b)).linewidth(LW))

    def dot(self, p, r=2.6):
        self.d.add(elm.Dot(radius=r * SCALE).at(self.U(p)))

    def text(self, p, s, kind='net', ha='left', va='center'):
        self.texts.append((p, s, kind, ha, va))

    def gnd(self, p):
        self.d.add(AMS.GndTri(stroke=LW).at(self.U(p)))

    def vdd(self, p, label=True):
        self.d.add(AMS.VddTee(stroke=LW).at(self.U(p)))
        if label:
            self.text((p[0], p[1] - 13), 'VDD', 'vdd', ha='center', va='bottom')

    def _fet(self, cls, x, y, anchor, gate, name, sub, op, label_side=None):
        e = cls(stroke=LW)
        if gate == 'right':
            e = e.reverse()
        r = self.d.add(e.anchor(anchor).at(self.U((x, y))))
        t = {k: self.P(r.absanchors[k]) for k in ('drain', 'gate', 'source')}
        cy = (t['drain'][1] + t['source'][1]) / 2
        side = label_side or ('right' if gate == 'left' else 'left')
        dx = 5 if side == 'right' else -5
        ha = 'left' if side == 'right' else 'right'
        if name:
            self.text((x + dx, cy - 11), name, 'device', ha=ha)
        lines = [l for l in (sub, op) if l]
        for i, l in enumerate(lines):
            self.text((x + dx, cy + 11 * i), l, 'size' if i == 0 else 'op', ha=ha)
        return t

    def nmos(self, x, y_drain, gate='left', name=None, sub=None, op=None, label_side=None):
        """NMOS with its drain at (x, y_drain); source 36 pt below; gate lead end 27 pt to the gate side."""
        return self._fet(AMS.NMOS2A, x, y_drain, 'drain', gate, name, sub, op, label_side)

    def pmos(self, x, y_source, gate='left', name=None, sub=None, op=None, label_side=None):
        """PMOS with its source at (x, y_source); drain 36 pt below."""
        return self._fet(AMS.PMOS2A, x, y_source, 'source', gate, name, sub, op, label_side)

    def res(self, a, b, name=None, value=None, side='right'):
        el = AMS.Resistor(stroke=LW).at(self.U(a)).to(self.U(b))
        self.d.add(el)
        self._passive_label(a, b, name, value, side)

    def cap(self, a, b, name=None, value=None, side='below'):
        el = AMS.Capacitor(stroke=LW).at(self.U(a)).to(self.U(b))
        self.d.add(el)
        self._passive_label(a, b, name, value, side)

    def _passive_label(self, a, b, name, value, side):
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        off = {'right': (9, 0, 'left'), 'left': (-9, 0, 'right'), 'below': (0, 16, 'center'), 'above': (0, -16, 'center')}[side]
        if name:
            self.text((mx + off[0], my + off[1] - (5.5 if value and side in ('left', 'right') else 0)), name, 'device', ha=off[2])
        if value:
            dy = 5.5 if side in ('left', 'right') else (11 if side == 'below' else -11)
            self.text((mx + off[0], my + off[1] + dy), value, 'size', ha=off[2])

    # ------------------------------------------------------------------ output
    def save(self, out, dpi=300, margin=12):
        # widen the drawing bbox to cover the texts (rough text extents) with invisible lines
        xs, ys = [], []
        for (x, y), s, kind, ha, va in self.texts:
            w = 0.6 * STYLES[kind]['fontsize'] * len(s)
            x0 = x if ha == 'left' else (x - w if ha == 'right' else x - w / 2)
            xs += [x0, x0 + w]; ys += [y - STYLES[kind]['fontsize'], y + STYLES[kind]['fontsize']]
        if xs:
            self.d.add(elm.Line().at(self.U((min(xs) - margin, min(ys) - margin))).to(self.U((min(xs) - margin + 0.01, min(ys) - margin))).color('white').linewidth(0.01))
            self.d.add(elm.Line().at(self.U((max(xs) + margin, max(ys) + margin))).to(self.U((max(xs) + margin + 0.01, max(ys) + margin))).color('white').linewidth(0.01))
        f = self.d.draw(show=False)
        # schemdraw can leave the figure at matplotlib's default size (6.4 x 4.8 in); then a wide sheet is
        # shrunk while the 9 pt labels are not, and labels overlap. Size the figure from the axes limits so
        # one Visio point is one output point.
        x1, x2 = f.ax.get_xlim(); y1, y2 = f.ax.get_ylim()
        f.fig.set_size_inches(abs(x2 - x1) * PT_PER_UNIT / 72, abs(y2 - y1) * PT_PER_UNIT / 72)
        f.ax.set_position([0, 0, 1, 1])
        for (x, y), s, kind, ha, va in self.texts:
            st = dict(STYLES[kind])
            fam = st.pop('family')
            ux, uy = self.U((x, y))
            f.ax.text(ux, uy, s, ha=ha, va=va, fontfamily=fam, zorder=5, **st)
        f.fig.savefig(out, dpi=dpi, bbox_inches='tight', pad_inches=0.05, facecolor='white')
