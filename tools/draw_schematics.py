#!/usr/bin/env python3
"""
draw_schematics.py - transistor-level schematics of the AMPICBench circuits, drawn with SchemDraw and the AMS symbol
library (tools/ams_symbols, helper classes in ams_symbols.py and analog_sheet.py).

Device names, sizes and multipliers come from each circuit's <name>_cells.spice; operating-point currents and node
voltages come from an ngspice `op` run of the same netlist (tt, 27 C, VDD 1.8 V).  Parallel devices that share
all four terminals' nets are drawn as one symbol (e.g. "PM3 + PM2").

    python3 draw_schematics.py <AMPICBench root> <output dir> [results summary.json]

Needs: schemdraw, matplotlib, ngspice (for the operating point) and the sky130 models used by the netlists.
"""
import json, os, re, subprocess, sys, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_sheet import Sheet

ROOT, OUT = sys.argv[1], sys.argv[2]
SUMMARY = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else {}
WORK = os.path.join(OUT, '_op')


# ---------------------------------------------------------------------------------------------- netlist + op data
SI = {'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, 'k': 1e3, 'K': 1e3, 'meg': 1e6, '': 1}


class Design:
    def __init__(self, name):
        self.name = name
        self.dir = next(os.path.join(r) for r, _, f in os.walk(ROOT) if f"{name}.cir" in f)
        self.cir = open(os.path.join(self.dir, f'{name}.cir')).read()
        cells = open(os.path.join(self.dir, f'{name}_cells.spice')).read()
        self.sub = {}
        for m in re.finditer(r'^\.subckt (\S+) (.*?)\n(.*?)^\.ends', cells, flags=re.M | re.S):
            devs = {}
            for l in m.group(3).strip().splitlines():
                t = l.split()
                if t[0].startswith('X') and any('=' in x for x in t):
                    kv = dict(x.split('=') for x in t if '=' in x)
                    devs[t[0][1:]] = dict(W=float(kv['W']), L=float(kv['L']), m=float(kv['m']), n='nfet' in t[5])
                elif t[0][0] in 'CR':
                    devs[t[0]] = dict(val=float(t[3]))
            self.sub[m.group(1)] = devs
        self.params = dict(re.findall(r'(\w+)=([0-9.]+[a-zA-Z]*)', ' '.join(re.findall(r'^\.param (.*)$', self.cir, flags=re.M))))
        self.run_op()

    def run_op(self):
        wd = os.path.join(WORK, self.name)
        os.makedirs(wd, exist_ok=True)
        # keep only op + the device dump (skip the long ac/dc sweeps)
        txt = re.sub(r'\.control.*', '.control\nset wr_singlescale\nset wr_vecnames\noption numdgt=7\nop\n'
                     'print all > op_nodes.txt\nshow m : id > op_devices.txt\n.endc\n.end\n', self.cir, flags=re.S)
        open(os.path.join(wd, 'op.cir'), 'w').write(txt)
        open(os.path.join(wd, '.spiceinit'), 'w').write('set ngbehavior=hsa\nset ng_nomodcheck\nset skywaterpdk\noption noinit\n')
        subprocess.run(['ngspice', '-b', 'op.cir'], cwd=wd, capture_output=True, text=True, timeout=300)
        self.id, self.node = {}, {}
        dev = open(os.path.join(wd, 'op_devices.txt')).read()
        for blk in re.split(r'BSIM4v5.*\n', dev)[1:]:
            names = re.search(r'model\s+(.*)', blk).group(1).split()
            names = [n.split(':')[0] for n in names]
            ids = re.search(r'\n\s*id\s+(.*)', blk).group(1).split()
            for n, i in zip(names, ids):
                self.id[n] = abs(float(i))
        for l in open(os.path.join(wd, 'op_nodes.txt')):
            m = re.match(r'(\S+) = (\S+)$', l.strip())
            if m and '#' not in m.group(1) and not m.group(1).startswith('m.'):
                self.node[m.group(1)] = float(m.group(2))

    def value(self, key):
        v = self.params.get(key)
        if v is None:
            return None
        m = re.match(r'([0-9.]+)([a-zA-Z]*)', v)
        return float(m.group(1)) * SI.get(m.group(2), 1)


def eng(v, unit):
    for s, k in (('', 1), ('m', 1e-3), ('u', 1e-6), ('n', 1e-9), ('p', 1e-12), ('f', 1e-15)):
        if v >= k * 0.999 or s == 'f':
            return f'{v / k:g} {s}{unit}'


def ohm(v):
    return f'{v / 1e3:g} kΩ' if v >= 1e3 else f'{v:g} Ω'


class Lab:
    """labels for devices of one subcircuit instance: sizes from the netlist, current from the op run"""
    def __init__(self, d, sub, prefix):
        self.d, self.sub, self.prefix = d, sub, prefix

    def __call__(self, *names, sub=None):
        devs = [self.d.sub[sub or self.sub][n] for n in names]
        m = sum(x['m'] for x in devs)
        w, l = devs[0]['W'], devs[0]['L']
        assert all(x['W'] == w and x['L'] == l for x in devs), names
        size = (f'{m:g} x ' if m != 1 else '') + f'{w:g}/{l:g}'
        i = sum(self.d.id.get(self.prefix + 'x' + n.lower(), float('nan')) for n in names)
        return dict(name=' + '.join(names), sub=size, op=f'{i * 1e6:.1f} uA')

    def v(self, net):
        x = self.d.node.get(self.prefix + net)
        return None if x is None else f'{x:.3f} V'


def fet(s, kind, x, y, lb, gate, side=None):
    f = s.nmos if kind == 'n' else s.pmos
    return f(x, y, gate=gate, label_side=side, **lb)


def gate_net(s, g, side, name):
    dx = -12 if side == 'left' else 12
    s.wire(g, (g[0] + dx, g[1]))
    s.text((g[0] + dx + (-3 if side == 'left' else 3), g[1]), name, 'net', ha='right' if side == 'left' else 'left')


def node(s, p, name, val=None, side='right', dy=0):
    s.dot(p)
    x = p[0] + (5 if side == 'right' else -5)
    ha = 'left' if side == 'right' else 'right'
    s.text((x, p[1] - 6 + dy), name, 'net', ha=ha)
    if val:
        s.text((x, p[1] + 5 + dy), val, 'op', ha=ha)


def box(s, x0, x1, y0, y1, lines):
    s.wire((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))
    xm = (x0 + x1) / 2
    for i, (t, k) in enumerate(lines):
        s.text((xm, y0 + 24 + 13 * i), t, k, ha='center')
    return xm


def bias_blocks(s, d, bx, by, ports, vb):
    """Bias_gen and Cascode_bias as box symbols (hierarchy rule), Vbiasp between them, selected Vbias<k> out"""
    bg = d.sub['Bias_gen']
    ib = d.value('Ibias')
    cb = d.sub['Cascode_bias']
    bx0, bx1, by0, by1 = bx, bx + 90, by, by + 99
    xm = box(s, bx0, bx1, by0, by1, [('Bias_gen', 'device'), (f"Ibias {eng(ib, 'A')}" if ib else '', 'size'),
                                     (f"NM0 {bg['NM0']['W']:g}/{bg['NM0']['L']:g}", 'size'), (f"PM0 {bg['PM0']['W']:g}/{bg['PM0']['L']:g}", 'size')])
    s.wire((xm, by0), (xm, by0 - 27)); s.vdd((xm, by0 - 27))
    s.wire((xm, by1), (xm, by1 + 18)); s.gnd((xm, by1 + 18))
    s.text((bx1 + 36, by0 + 20), 'Vbiasp', 'net', ha='center', va='bottom')
    s.text((bx0 - 4, by0 + 24), 'Vbiasn', 'net', ha='right'); s.text((bx0 - 4, by0 + 36), '(unused)', 'op', ha='right')
    cx0, cx1, cy0 = bx1 + 72, bx1 + 72 + 180, by0
    cy1 = by0 + 72 + 18 * len(ports) + 18
    cm = box(s, cx0, cx1, cy0, cy1, [('Cascode_bias', 'device'), (f"wide swing: Ln_MWS {cb['NM0']['L']:g} um", 'size'),
                                     (f"Lp_MWS {cb['PM2']['L']:g} um", 'size')])
    s.wire((cm, cy0), (cm, cy0 - 27)); s.vdd((cm, cy0 - 27))
    s.wire((cm, cy1), (cm, cy1 + 18)); s.gnd((cm, cy1 + 18))
    yv = by0 + 24
    s.wire((bx1, yv), (cx0, yv))
    for i, (nm, key) in enumerate(ports):
        y = cy0 + 72 + 18 * i
        s.wire((cx1, y), (cx1 + 18, y))
        s.text((cx1 - 4, y), nm, 'net', ha='right')
        if vb(key):
            s.text((cx1 + 22, y), vb(key), 'op')
    return bx0, cx1


def title(s, x, y, text):
    s.text((x, y), text, 'title', ha='center', va='bottom')


def notes(s, x, y, lines):
    for i, t in enumerate(lines):
        s.text((x, y + 13 * i), t, 'net')


def metrics_line(name):
    r = SUMMARY.get(name)
    if not r:
        return None
    if 'stb' in r:
        m = r['stb']
        return (f"Simulated (ngspice, tt, 27 C): loop gain {m['dc_gain_db']:.1f} dB, UGF {m['ugf_hz'] / 1e6:.1f} MHz, "
                f"PM {m['phase_margin_deg']:.1f} deg, GM {m['gain_margin_db']:.1f} dB.")
    m = r['ac']
    return f"Simulated (ngspice, tt, 27 C): open-loop gain {m['dc_gain_db']:.1f} dB, UGF {m['ugf_hz'] / 1e6:.1f} MHz."


def save(s, name):
    for ext in ('png', 'pdf', 'svg'):
        s.save(os.path.join(OUT, f'{name}.{ext}'))


SIZE_NOTE = 'Sizes: multiplier x W/L in um (nf = 1), sky130 LVT devices. Currents and node voltages: tt, 27 °C, VDD = 1.8 V.'


# ---------------------------------------------------------------------------------------------- cores
def core_diff(s, L, wiring):
    """NMOS pair + PMOS mirror + cascoded tail (Diffamp1 / Opamp1 first stage).  wiring: dict of device names and labels"""
    XL, XR, XT = 0, 162, 81
    YP, YN, YT = 18, 108, 162
    w = wiring
    s.vdd((XL, YP)); s.vdd((XR, YP))
    pd = fet(s, 'p', XL, YP, L(w['pm_d']), 'right', 'left')
    po = fet(s, 'p', XR, YP, L(w['pm_o']), 'left', 'right')
    s.wire(pd['gate'], po['gate'])
    xd = XL + 45
    s.dot((xd, pd['gate'][1])); s.wire((xd, pd['gate'][1]), (xd, 72), (XL, 72)); s.dot((XL, 72))
    s.text((XL - 5, 76), w['net_d'], 'net', ha='right', va='top')
    if L.v(w['net_d']): s.text((XL - 5, 87), L.v(w['net_d']), 'op', ha='right', va='top')
    s.wire(pd['drain'], (XL, YN)); s.wire(po['drain'], (XR, YN))
    nd = fet(s, 'n', XL, YN, L(w['n_d']), 'left', 'right')
    no = fet(s, 'n', XR, YN, L(w['n_o']), 'right', 'left')
    gate_net(s, nd['gate'], 'left', w['g_d']); gate_net(s, no['gate'], 'right', w['g_o'])
    s.wire(nd['source'], (XL, YT), (XR, YT), no['source'])
    s.dot((XT, YT)); s.text((XT + 5, YT - 4), w['net_t'], 'net', va='bottom')
    if L.v(w['net_t']): s.text((XT + 30, YT - 4), L.v(w['net_t']), 'op', va='bottom')
    t1 = fet(s, 'n', XT, YT, L(w['tail_c']), 'left', 'right'); gate_net(s, t1['gate'], 'left', 'Vbias<3>')
    t2 = fet(s, 'n', XT, YT + 36, L(w['tail']), 'left', 'right'); gate_net(s, t2['gate'], 'left', 'Vbias<4>')
    s.gnd(t2['source'])
    return dict(out=(XR, YN - 27), XR=XR, y_out=81)


def core_fc(s, L, w):
    """folded-cascode first stage (FC_Amp); returns the output node (right branch) point"""
    XL, XR, XA, XB, XT = 0, 324, 90, 234, 162
    s.vdd((XL, 18)); s.vdd((XR, 18))
    pl = fet(s, 'p', XL, 18, L('PM3', 'PM2'), 'right', 'left')
    pr = fet(s, 'p', XR, 18, L('PM1', 'PM0'), 'left', 'right')
    s.wire(pl['gate'], pr['gate'])
    s.dot((XT, pl['gate'][1])); s.wire((XT, pl['gate'][1]), (XT, pl['gate'][1] - 14))
    s.text((XT, pl['gate'][1] - 17), 'net013', 'net', ha='center', va='bottom')
    YF = 63
    s.wire(pl['drain'], (XL, 72)); s.wire(pr['drain'], (XR, 72))
    s.dot((XL, YF)); s.dot((XR, YF))
    s.text((XL - 5, YF), 'net010', 'net', ha='right')
    if L.v('net010'): s.text((XL - 50, YF), L.v('net010'), 'op', ha='right')
    s.text((XR + 5, YF), 'net016', 'net')
    if L.v('net016'): s.text((XR + 48, YF), L.v('net016'), 'op')
    pm4 = fet(s, 'p', XL, 72, L('PM4'), 'left', 'right'); gate_net(s, pm4['gate'], 'left', 'Vbias<2>')
    pm5 = fet(s, 'p', XR, 72, L('PM5'), 'right', 'left'); gate_net(s, pm5['gate'], 'right', 'Vbias<2>')
    Y13 = 126
    s.wire(pm4['drain'], (XL, 144)); s.wire(pm5['drain'], (XR, 144))
    s.dot((XL, Y13)); s.wire((XL, Y13), (XL - 30, Y13))
    s.text((XL - 33, Y13 - 6), 'net013', 'net', ha='right')
    if L.v('net013'): s.text((XL - 33, Y13 + 5), L.v('net013'), 'op', ha='right')
    nm2 = fet(s, 'n', XL, 144, L('NM2'), 'left', 'right'); gate_net(s, nm2['gate'], 'left', 'Vbias<3>')
    nm6 = fet(s, 'n', XL, 180, L('NM6'), 'left', 'right'); gate_net(s, nm6['gate'], 'left', 'Vbias<4>')
    s.gnd(nm6['source'])
    nm3 = fet(s, 'n', XR, 144, L('NM3'), 'right', 'left'); gate_net(s, nm3['gate'], 'right', 'Vbias<3>')
    nm8 = fet(s, 'n', XR, 180, L('NM8'), 'right', 'left'); gate_net(s, nm8['gate'], 'right', 'Vbias<4>')
    s.gnd(nm8['source'])
    YP = 252
    nm9 = fet(s, 'n', XA, YP, L('NM9'), 'left', 'right'); nm10 = fet(s, 'n', XB, YP, L('NM10'), 'right', 'left')
    gate_net(s, nm9['gate'], 'left', w['g9']); gate_net(s, nm10['gate'], 'right', w['g10'])
    s.wire((XA, YP), (XA, YF), (XL, YF)); s.wire((XB, YP), (XB, YF), (XR, YF))
    YT = YP + 54
    s.wire(nm9['source'], (XA, YT), (XB, YT), nm10['source'])
    s.dot((XT, YT)); s.text((XT + 5, YT - 4), 'net08', 'net', va='bottom')
    if L.v('net08'): s.text((XT + 34, YT - 4), L.v('net08'), 'op', va='bottom')
    t1 = fet(s, 'n', XT, YT, L('NM0', 'NM1'), 'left', 'right'); gate_net(s, t1['gate'], 'left', 'Vbias<3>')
    t2 = fet(s, 'n', XT, YT + 36, L('NM4', 'NM5'), 'left', 'right'); gate_net(s, t2['gate'], 'left', 'Vbias<4>')
    s.gnd(t2['source'])
    return dict(out=(XR, Y13), XR=XR, y_out=Y13)


def core_tele(s, L, w):
    """telescopic first stage with replica (bias) branch on the left; returns the output node point"""
    XQ, XL, XR = 0, 243, 423
    XT = (XL + XR) / 2
    YN, YD, YP, YT = 99, 108, 144, 198
    s.vdd((XL, 18)); s.vdd((XR, 18))
    pm2 = fet(s, 'p', XL, 18, L('PM2'), 'right', 'left'); pm3 = fet(s, 'p', XR, 18, L('PM3'), 'left', 'right')
    s.wire(pm2['gate'], pm3['gate'])
    s.wire(pm2['drain'], (XL, 54)); s.wire(pm3['drain'], (XR, 54))
    pm1 = fet(s, 'p', XL, 54, L('PM1'), 'left', 'right'); gate_net(s, pm1['gate'], 'left', 'Vbias<2>')
    pm0 = fet(s, 'p', XR, 54, L('PM0'), 'right', 'left'); gate_net(s, pm0['gate'], 'right', 'Vbias<2>')
    s.wire(pm1['drain'], (XL, YD)); s.wire(pm0['drain'], (XR, YD))
    s.dot((XT, pm2['gate'][1])); s.wire((XT, pm2['gate'][1]), (XT, YN), (XL, YN)); s.dot((XL, YN))
    s.text((XT + 4, YN - 6), 'net082', 'net')
    if L.v('net082'): s.text((XT + 4, YN + 5), L.v('net082'), 'op')
    nm2 = fet(s, 'n', XL, YD, L('NM2'), 'left', 'right'); gate_net(s, nm2['gate'], 'left', 'Vbias5')
    nm3 = fet(s, 'n', XR, YD, L('NM3'), 'right', 'left'); gate_net(s, nm3['gate'], 'right', 'Vbias5')
    nm0 = fet(s, 'n', XL, YP, L('NM0'), 'left', 'right'); gate_net(s, nm0['gate'], 'left', w['g0'])
    nm1 = fet(s, 'n', XR, YP, L('NM1'), 'right', 'left'); gate_net(s, nm1['gate'], 'right', w['g1'])
    s.wire(nm0['source'], (XL, YT), (XR, YT), nm1['source'])
    s.dot((XT, YT)); s.text((XT + 5, YT - 4), 'net8', 'net', va='bottom')
    if L.v('net8'): s.text((XT + 30, YT - 4), L.v('net8'), 'op', va='bottom')
    t1 = fet(s, 'n', XT, YT, L('NM4', 'NM5'), 'left', 'right'); gate_net(s, t1['gate'], 'left', 'Vbias<3>')
    t2 = fet(s, 'n', XT, YT + 36, L('NM6', 'NM7'), 'left', 'right'); gate_net(s, t2['gate'], 'left', 'Vbias<4>')
    s.gnd(t2['source'])
    # replica
    s.vdd((XQ, 18))
    pm4 = fet(s, 'p', XQ, 18, L('PM4'), 'right', 'left')
    s.wire(pm4['drain'], (XQ, 54))
    pm5 = fet(s, 'p', XQ, 54, L('PM5'), 'left', 'right'); gate_net(s, pm5['gate'], 'left', 'Vbias<2>')
    s.wire(pm5['drain'], (XQ, YD))
    nm8 = fet(s, 'n', XQ, YD, L('NM8'), 'right', 'left')
    XV = XQ + 81
    s.wire(pm4['gate'], (XV, pm4['gate'][1]), (XV, nm8['gate'][1]), nm8['gate'])
    s.dot((XV, YN)); s.wire((XV, YN), (XQ, YN)); s.dot((XQ, YN))
    s.text((XV + 4, YN - 6), 'Vbias5', 'net')
    if L.v('vbias5'): s.text((XV + 4, YN + 5), L.v('vbias5'), 'op')
    nm9 = fet(s, 'n', XQ, YP, L('NM9'), 'left', 'right'); gate_net(s, nm9['gate'], 'left', 'VCM')
    s.wire(nm9['source'], (XQ, YT))
    nm10 = fet(s, 'n', XQ, YT, L('NM10'), 'left', 'right'); gate_net(s, nm10['gate'], 'left', 'Vbias<3>')
    nm11 = fet(s, 'n', XQ, YT + 36, L('NM11'), 'left', 'right'); gate_net(s, nm11['gate'], 'left', 'Vbias<4>')
    s.gnd(nm11['source'])
    s.text((XQ, -30), 'Bias replica', 'device', ha='center', va='bottom')
    return dict(out=(XR, YN), XR=XR, y_out=YN)


def load_cap(s, p, d):
    cl = None
    m = re.search(r'^CC0 vout 0 (\S+)', d.cir, flags=re.M)
    if m:
        v = m.group(1).strip('{}')
        cl = d.value(v) if re.match(r'[A-Za-z_]', v) else None
        if cl is None:
            mm = re.match(r'([0-9.]+)([a-zA-Z]*)', v)
            cl = float(mm.group(1)) * SI[mm.group(2)]
    s.cap(p, (p[0], p[1] + 36), 'CL', eng(cl, 'F') if cl else '', side='right'); s.gnd((p[0], p[1] + 36))
    return cl


# ---------------------------------------------------------------------------------------------- circuits
def draw_bias_gen_sheet():
    d = Design('sim_Bias_gen')
    L = Lab(d, 'Bias_gen', 'xi0.')
    s = Sheet()
    ib = d.value('Ibias')
    XN, XP = 0, 198
    # NMOS diode fed from VDD by II0
    s.vdd((XN, 18))
    s.wire((XN, 18), (XN, 27)); isrc(s, (XN, 27), (XN, 72), 'II0', eng(ib, 'A'))
    s.wire((XN, 72), (XN, 99)); s.dot((XN, 99))
    nm0 = fet(s, 'n', XN, 99, L('NM0'), 'right', 'left')
    # diode connection: gate to drain node
    s.wire(nm0['gate'], (nm0['gate'][0], 99), (XN, 99))
    s.text((XN - 5, 94), 'Vbiasn', 'net', ha='right')
    if L.v('vbiasn'): s.text((XN - 5, 105), L.v('vbiasn'), 'op', ha='right', va='top')
    s.gnd(nm0['source'])
    # PMOS diode sinking II1 to ground
    s.vdd((XP, 18))
    pm0 = fet(s, 'p', XP, 18, L('PM0'), 'left', 'right')
    s.wire(pm0['drain'], (XP, 63)); s.dot((XP, 63))
    s.wire(pm0['gate'], (pm0['gate'][0], 63), (XP, 63))
    s.text((XP + 5, 58), 'Vbiasp', 'net')
    if L.v('vbiasp'): s.text((XP + 5, 69), L.v('vbiasp'), 'op', va='top')
    s.wire((XP, 63), (XP, 81)); isrc(s, (XP, 81), (XP, 126), 'II1', eng(ib, 'A'))
    s.gnd((XP, 126))
    title(s, (XN + XP) / 2, -48, 'Bias_gen: two diode-connected references, Vbiasn (NMOS) and Vbiasp (PMOS)')
    notes(s, -90, 189, [SIZE_NOTE, 'II0 and II1 are ideal current sources of Ibias = ' + eng(ib, 'A') + '.'])
    save(s, 'Bias_gen')


def isrc(s, a, b, name, val):
    """ideal current source (circle with arrow), top terminal a, bottom terminal b (y down)"""
    import schemdraw.elements as elm
    s.d.add(elm.SourceI().at(s.U(a)).to(s.U(b)).linewidth(1.5))
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    s.text((mx + 14, my - 5.5), name, 'device', ha='left'); s.text((mx + 14, my + 5.5), val, 'size', ha='left')


def draw_cascode_bias():
    d = Design('sim_Cascode_bias')
    L = Lab(d, 'Cascode_bias', 'xi2.')
    s = Sheet()
    XC, XD, XE, XF = 0, 165, 330, 495
    ng = lambda k: L.v(k)

    def vnode(p, name, key, side='right', dy=0):
        node(s, p, name, ng(key), side, dy)
    # Vbiasp input net: PM0 / PM1 gates
    # C: PM0 -> NM9 (diode) -> NM0 (diode, long)  : Vbias<3>
    s.vdd((XC, 18))
    pm0 = fet(s, 'p', XC, 18, L('PM0'), 'left', 'right'); gate_net(s, pm0['gate'], 'left', 'Vbiasp')
    s.wire(pm0['drain'], (XC, 90))
    nm9 = fet(s, 'n', XC, 90, L('NM9'), 'right', 'left')
    s.wire(nm9['gate'], (nm9['gate'][0], 72), (XC, 72)); s.dot((XC, 72))
    s.text((XC - 5, 66), 'net2', 'net', ha='right')
    if ng('net2'): s.text((XC - 5, 77), ng('net2'), 'op', ha='right')
    s.wire(nm9['source'], (XC, 144))
    nm0 = fet(s, 'n', XC, 144, L('NM0'), 'right', 'left')
    s.wire(nm0['gate'], (nm0['gate'][0], 135), (XC, 135)); s.dot((XC, 135))
    s.text((XC + 5, 129), 'Vbias<3>', 'net')
    if ng('vbias_3'): s.text((XC + 5, 140), ng('vbias_3'), 'op')
    s.gnd(nm0['source'])
    # D: PM1 -> NM11 (diode) -> NM1 (cascode) -> NM2 : Vbias<4>
    s.vdd((XD, 18))
    pm1 = fet(s, 'p', XD, 18, L('PM1'), 'left', 'right'); gate_net(s, pm1['gate'], 'left', 'Vbiasp')
    s.wire(pm1['drain'], (XD, 90))
    nm11 = fet(s, 'n', XD, 90, L('NM11'), 'right', 'left')
    s.wire(nm11['gate'], (nm11['gate'][0], 72), (XD, 72)); s.dot((XD, 72))
    s.text((XD - 5, 66), 'net4', 'net', ha='right')
    if ng('net4'): s.text((XD - 5, 77), ng('net4'), 'op', ha='right')
    s.wire(nm11['source'], (XD, 144))
    s.dot((XD, 135)); s.text((XD + 5, 129), 'Vbias<4>', 'net')
    if ng('vbias_4'): s.text((XD + 5, 140), ng('vbias_4'), 'op')
    nm1 = fet(s, 'n', XD, 144, L('NM1'), 'left', 'right'); gate_net(s, nm1['gate'], 'left', 'Vbias<3>')
    nm2 = fet(s, 'n', XD, 180, L('NM2'), 'left', 'right'); gate_net(s, nm2['gate'], 'left', 'Vbias<4>')
    s.gnd(nm2['source'])
    # E: PM2 (long diode) -> NM3 (cascode) -> NM4 : Vbias<2>
    s.vdd((XE, 18))
    pm2 = fet(s, 'p', XE, 18, L('PM2'), 'left', 'right')
    s.wire(pm2['gate'], (pm2['gate'][0], 72), (XE, 72)); s.dot((XE, 72))
    s.text((XE + 5, 78), 'Vbias<2>', 'net', va='top')
    if ng('vbias_2'): s.text((XE + 5, 89), ng('vbias_2'), 'op', va='top')
    s.wire(pm2['drain'], (XE, 180))
    nm3 = fet(s, 'n', XE, 180, L('NM3'), 'left', 'right'); gate_net(s, nm3['gate'], 'left', 'Vbias<3>')
    nm4 = fet(s, 'n', XE, 216, L('NM4'), 'left', 'right'); gate_net(s, nm4['gate'], 'left', 'Vbias<4>')
    s.gnd(nm4['source'])
    # F: PM3 -> PM4 (cascode, Vbias<2>) -> NM5 -> NM6 : Vbias<1>
    s.vdd((XF, 18))
    pm3 = fet(s, 'p', XF, 18, L('PM3'), 'right', 'left')
    pm4 = fet(s, 'p', XF, 54, L('PM4'), 'left', 'right'); gate_net(s, pm4['gate'], 'left', 'Vbias<2>')
    s.text((XF + 5, 49), 'net5', 'net')
    YV1 = 126
    s.wire(pm4['drain'], (XF, 180))
    xr = XF + 90
    s.wire(pm3['gate'], (xr, pm3['gate'][1]), (xr, YV1), (XF, YV1)); s.dot((XF, YV1))
    s.text((XF + 5, YV1 + 6), 'Vbias<1>', 'net', va='top')
    if ng('vbias_1'): s.text((XF + 5, YV1 + 17), ng('vbias_1'), 'op', va='top')
    nm5 = fet(s, 'n', XF, 180, L('NM5'), 'left', 'right'); gate_net(s, nm5['gate'], 'left', 'Vbias<3>')
    nm6 = fet(s, 'n', XF, 216, L('NM6'), 'left', 'right'); gate_net(s, nm6['gate'], 'left', 'Vbias<4>')
    s.gnd(nm6['source'])
    title(s, (XC + XF) / 2, -48, 'Cascode_bias: wide-swing cascode bias network, outputs Vbias<1> to Vbias<4>')
    notes(s, -60, 300, [SIZE_NOTE,
                        f"Long-channel diodes: NM0 L = {d.sub['Cascode_bias']['NM0']['L']:g} um (Ln_MWS), PM2 L = {d.sub['Cascode_bias']['PM2']['L']:g} um (Lp_MWS). "
                        'Vbiasp comes from Bias_gen.',
                        'Vbias<3> and Vbias<4> bias the NMOS cascode / sink pairs, Vbias<1> and Vbias<2> the PMOS cascodes.'])
    save(s, 'Cascode_bias')


def amp_bias(s, d, bx, by, ports, L1):
    vb = lambda k: ('%.3f V' % d.node[k]) if k in d.node else None
    return bias_blocks(s, d, bx, by, ports, vb)


def draw_diffamp1():
    d = Design('sim_Diffamp1')
    L = Lab(d, 'Diffamp1', 'xi1.')
    s = Sheet()
    w = dict(pm_d='PM1', pm_o='PM0', net_d='net9', n_d='NM0', n_o='NM1', g_d='vp (+)', g_o='vm (−)', net_t='net10', tail_c='NM2', tail='NM3')
    r = core_diff(s, L, w)
    # PM0 / NM1 drains are the output (vout)
    XR = r['XR']
    s.dot((XR, 81)); xo = XR + 99
    s.wire((XR, 81), (xo + 36, 81)); s.dot((xo, 81))
    s.text((xo + 40, 75), 'vout', 'net')
    if d.node.get('vout'): s.text((xo + 40, 86), '%.3f V' % d.node['vout'], 'op')
    load_cap(s, (xo, 81), d)
    bx0, cx1 = amp_bias(s, d, -540, 108, [('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L)
    title(s, (bx0 + xo) / 2, -48, 'Diffamp1: differential amplifier with NMOS pair, PMOS mirror load and cascoded tail')
    notes(s, bx0, 300, [SIZE_NOTE, metrics_line('sim_Diffamp1') or ''])
    save(s, 'Diffamp1')


def stage2(s, d, L2, out, xb, xs, ys, yc, rz, cc, rz_name='RR1', cc_name='CC1', ngate=None):
    """class-A second stage (PM, cascoded NMOS sink) with Rz + Cc; out is the first-stage output node (x, y)"""
    s.vdd((xs, ys))
    pm = fet(s, 'p', xs, ys, L2('PM2'), 'left', 'right')
    s.wire((xb, out[1]), (xb, pm['gate'][1]), pm['gate']) if out[1] != pm['gate'][1] else s.wire((xb, out[1]), pm['gate'])
    s.dot((xb, out[1]))
    if out[1] != yc:
        s.wire((xb, out[1]), (xb, yc), (xb + 9, yc))
    else:
        s.wire((xb, yc), (xb + 9, yc))
    s.res((xb + 9, yc), (xb + 45, yc), rz_name.replace('RR', 'R'), ohm(rz), side='above')
    s.wire((xb + 45, yc), (xb + 63, yc))
    s.cap((xb + 63, yc), (xb + 99, yc), cc_name.replace('CC', 'C'), eng(cc, 'F'), side='above')
    s.wire((xb + 99, yc), (xs, yc))
    y5 = yc + 27
    s.wire(pm['drain'], (xs, y5)); s.dot((xs, yc))
    nm5 = fet(s, 'n', xs, y5, L2('NM5'), 'left', 'right'); gate_net(s, nm5['gate'], 'left', 'Vbias<3>')
    nm4 = fet(s, 'n', xs, y5 + 36, L2('NM4'), 'left', 'right'); gate_net(s, nm4['gate'], 'left', 'Vbias<4>')
    s.gnd(nm4['source'])
    xo = xs + 72
    s.wire((xs, yc), (xo + 36, yc)); s.dot((xo, yc))
    s.text((xo + 40, yc - 6), 'vout', 'net')
    if d.node.get('vout'): s.text((xo + 40, yc + 5), '%.3f V' % d.node['vout'], 'op')
    load_cap(s, (xo, yc), d)
    return xo


def draw_opamp1():
    d = Design('sim_Opamp1_stb')
    L1 = Lab(d, 'Opamp1', 'xi1.')
    L2 = Lab(d, 'Opamp1', 'xi1.')
    s = Sheet()
    w = dict(pm_d='PM1', pm_o='PM0', net_d='net3', n_d='NM0', n_o='NM1', g_d='vm (−)', g_o='vp (+)', net_t='net1', tail_c='NM2', tail='NM3')
    r = core_diff(s, L1, w)
    XR = r['XR']; y8 = 81
    s.dot((XR, y8))
    s.text((XR + 70, y8 + 4), 'net08', 'net', va='top')
    if L1.v('net08'): s.text((XR + 70, y8 + 15), L1.v('net08'), 'op', va='top')
    # second stage; names are PM2/NM5/NM4 in the same subcircuit
    s.wire((XR, y8), (279, y8))
    xo = stage2(s, d, L2, (279, y8), 279, 396, 18, y8, d.sub['Opamp1']['RR0']['val'], d.sub['Opamp1']['CC0']['val'], 'R0', 'C0')
    bx0, cx1 = amp_bias(s, d, -540, 108, [('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L1)
    title(s, (bx0 + xo) / 2, -48, 'Opamp1: two-stage Miller opamp, differential first stage and class-A second stage')
    notes(s, bx0, 300, [SIZE_NOTE, 'Rz and Cc: Miller compensation between net08 and vout.', metrics_line('sim_Opamp1_stb') or ''])
    save(s, 'Opamp1')


def draw_2stg_diff():
    d = Design('sim_Opamp_2stg_Diff_ClassA_STB')
    L1 = Lab(d, 'Diffamp1', 'xi1.xi7.'); L2 = Lab(d, 'ClassA_stg_PMOS', 'xi1.xi6.')
    s = Sheet()
    w = dict(pm_d='PM1', pm_o='PM0', net_d='net9', n_d='NM0', n_o='NM1', g_d='vm (−)', g_o='vp (+)', net_t='net10', tail_c='NM2', tail='NM3')
    r = core_diff(s, L1, w)
    XR = r['XR']; y8 = 81
    s.dot((XR, y8)); s.text((XR + 70, y8 + 4), 'net1', 'net', va='top')
    if L2.v('net1') is not None and d.node.get('xi1.net3') is not None:
        pass
    s.wire((XR, y8), (279, y8))
    op = d.sub['Opamp_2stg_Diff_ClassA']
    xo = stage2(s, d, L2, (279, y8), 279, 396, 18, y8, op['RR1']['val'], op['CC1']['val'])
    bx0, cx1 = amp_bias(s, d, -540, 108, [('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L1)
    title(s, (bx0 + xo) / 2, -48, 'Opamp_2stg_Diff_ClassA: differential first stage + class-A second stage (STB testbench)')
    notes(s, bx0, 300, [SIZE_NOTE, 'First stage = Diffamp1, second stage = ClassA_stg_PMOS; Rz and Cc between the stage-1 output and vout. '
                        'Inputs are swapped at the top level because the second stage inverts.', metrics_line('sim_Opamp_2stg_Diff_ClassA_STB') or ''])
    save(s, 'Opamp_2stg_Diff_ClassA')


def draw_fc_amp():
    d = Design('sim_FC_Amp')
    L = Lab(d, 'FC_Amp', 'xi1.')
    s = Sheet()
    r = core_fc(s, L, dict(g9='vp (+)', g10='vm (−)'))
    XR, Y13 = r['out']
    xo = XR + 135
    s.dot((XR, Y13)); s.wire((XR, Y13), (xo + 36, Y13)); s.dot((xo, Y13))
    s.text((xo + 40, Y13 - 6), 'vout', 'net')
    if d.node.get('vout'): s.text((xo + 40, Y13 + 5), '%.3f V' % d.node['vout'], 'op')
    load_cap(s, (xo, Y13), d)
    bx0, cx1 = amp_bias(s, d, -580, 207, [('Vbias<2>', 'vbias_2'), ('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L)
    title(s, (bx0 + xo + 60) / 2, -48, 'FC_Amp: folded-cascode amplifier with NMOS input pair')
    notes(s, bx0, 415, [SIZE_NOTE, 'Self-biased PMOS mirror (gates on net013); PM4 / PM5 and NM2 / NM3 are the cascodes, NM6 / NM8 the sinks.',
                        metrics_line('sim_FC_Amp') or ''])
    save(s, 'FC_Amp')


def draw_2stg_fc():
    d = Design('sim_Opamp_2stg_FC_ClassA_STB')
    L1 = Lab(d, 'FC_Amp', 'xi1.xi7.'); L2 = Lab(d, 'ClassA_stg_PMOS', 'xi1.xi6.')
    s = Sheet()
    r = core_fc(s, L1, dict(g9='vm (−)', g10='vp (+)'))
    XR, Y13 = r['out']
    s.dot((XR, Y13)); s.text((XR + 8, Y13 - 4), 'net3', 'net', va='bottom')
    xb = XR + 108
    s.wire((XR, Y13), (xb, Y13))
    op = d.sub['Opamp_2stg_FC_ClassA']
    xs = XR + 270
    xo = stage2(s, d, L2, (xb, Y13), xb, xs, Y13 - 18, Y13 + 45, op['RR1']['val'], op['CC1']['val'])
    bx0, cx1 = amp_bias(s, d, -580, 207, [('Vbias<2>', 'vbias_2'), ('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L1)
    title(s, (bx0 + xo + 60) / 2, -48, 'Opamp_2stg_FC_ClassA: folded-cascode first stage + class-A second stage (STB testbench)')
    notes(s, bx0, 415, [SIZE_NOTE, 'First stage = FC_Amp, second stage = ClassA_stg_PMOS; Rz and Cc between net3 and vout. '
                        'Inputs are swapped at the top level because the second stage inverts.', metrics_line('sim_Opamp_2stg_FC_ClassA_STB') or ''])
    save(s, 'Opamp_2stg_FC_ClassA')


def draw_tele_amp():
    d = Design('sim_Telescopic_Amp')
    L = Lab(d, 'Telescopic_Amp', 'xi1.')
    s = Sheet()
    r = core_tele(s, L, dict(g0='vp (+)', g1='vm (−)'))
    XR, YN = r['out']
    xo = XR + 99
    s.dot((XR, YN)); s.wire((XR, YN), (xo + 36, YN)); s.dot((xo, YN))
    s.text((xo + 40, YN - 6), 'vout', 'net')
    if d.node.get('vout'): s.text((xo + 40, YN + 5), '%.3f V' % d.node['vout'], 'op')
    load_cap(s, (xo, YN), d)
    bx0, cx1 = amp_bias(s, d, -560, 180, [('Vbias<2>', 'vbias_2'), ('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L)
    title(s, (bx0 + xo + 60) / 2, -60, 'Telescopic_Amp: telescopic cascode amplifier with a bias replica for the NMOS cascodes')
    notes(s, bx0, 390, [SIZE_NOTE, 'Replica branch: NM9 on VCM and the NM8 diode generate Vbias5 for the NMOS cascodes NM2 / NM3.',
                        metrics_line('sim_Telescopic_Amp') or ''])
    save(s, 'Telescopic_Amp')


def draw_2stg_tele():
    d = Design('sim_Opamp_2stg_Telescopic_ClassA_STB')
    L1 = Lab(d, 'Telescopic_Amp', 'xi1.xi7.'); L2 = Lab(d, 'ClassA_stg_PMOS', 'xi1.xi6.')
    s = Sheet()
    r = core_tele(s, L1, dict(g0='vm (−)', g1='vp (+)'))
    XR, YN = r['out']
    s.dot((XR, YN)); s.text((XR + 8, YN - 6), 'net3', 'net', va='bottom')
    xb = XR + 90
    s.wire((XR, YN), (xb, YN))
    op = d.sub['Opamp_2stg_Telescopic_ClassA']
    xs = XR + 252
    xo = stage2(s, d, L2, (xb, YN), xb, xs, YN - 18, YN + 45, op['RR1']['val'], op['CC1']['val'])
    bx0, cx1 = amp_bias(s, d, -560, 180, [('Vbias<2>', 'vbias_2'), ('Vbias<3>', 'vbias_3'), ('Vbias<4>', 'vbias_4')], L1)
    title(s, (bx0 + xo + 60) / 2, -60, 'Opamp_2stg_Telescopic_ClassA: telescopic first stage + class-A second stage (STB testbench)')
    notes(s, bx0, 390, [SIZE_NOTE, 'First stage = Telescopic_Amp, second stage = ClassA_stg_PMOS; Rz and Cc between net3 and vout. '
                        'Inputs are swapped at the top level because the second stage inverts.', metrics_line('sim_Opamp_2stg_Telescopic_ClassA_STB') or ''])
    save(s, 'Opamp_2stg_Telescopic_ClassA')


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for fn in (draw_bias_gen_sheet, draw_cascode_bias, draw_diffamp1, draw_opamp1, draw_2stg_diff,
               draw_fc_amp, draw_2stg_fc, draw_tele_amp, draw_2stg_tele):
        try:
            fn(); print('ok', fn.__name__)
        except Exception as e:
            import traceback; traceback.print_exc(); print('FAILED', fn.__name__, e)
    shutil.rmtree(WORK, ignore_errors=True)
