#!/usr/bin/env python3
"""Run stb (loop gain) and tran (unity-gain step) sims for AMPICBench opamps and plot them.

Usage: run_sims.py <AMPICBench root> <output root | --in-place>
Works on copies of the netlists; the originals are never modified.
"""
import json, os, re, shutil, subprocess, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC, OUT = sys.argv[1], sys.argv[2]
INPLACE = OUT == "--in-place"      # write <circuit dir>/results/ next to each netlist
if INPLACE:
    import tempfile
    OUT = tempfile.mkdtemp(prefix="ampicbench_")
STB = ["sim_Opamp1_stb", "sim_Opamp_2stg_Diff_ClassA_STB",
       "sim_Opamp_2stg_FC_ClassA_STB", "sim_Opamp_2stg_Telescopic_ClassA_STB"]
OPEN = ["sim_Diffamp1", "sim_Telescopic_Amp", "sim_FC_Amp"]
SPICEINIT = "set ngbehavior=hsa\nset ng_nomodcheck\nset skywaterpdk\noption noinit\n"
BLUE, ORANGE, GREY = "#2563c7", "#d9622b", "#6b7280"

plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.6,
                     "lines.linewidth": 1.8, "figure.dpi": 140})


def run(workdir, cir):
    with open(os.path.join(workdir, ".spiceinit"), "w") as f:
        f.write(SPICEINIT)
    r = subprocess.run(["ngspice", "-b", cir], cwd=workdir, capture_output=True, text=True, timeout=600)
    open(os.path.join(workdir, "ngspice.log"), "w").write(r.stdout + r.stderr)
    return r


def find_cir(name):
    for root, _, files in os.walk(SRC):
        if f"{name}.cir" in files:
            return os.path.join(root, f"{name}.cir")
    raise FileNotFoundError(name)


def param(text, key, default):
    m = re.search(r"\b%s=([0-9.]+[a-zA-Z]*)" % key, text)
    return m.group(1) if m else default


def tran_control(extra=""):
    return (".control\nset wr_singlescale\nset wr_vecnames\noption numdgt=7\n"
            "tran 0.1n 4u\nwrdata tran.txt v(vin) v(vout)\n.endc\n.end\n")


def patch_tran_stb(text):
    text = re.sub(r"^Vprb_inj .*$", "Vprb_inj net6 vout 0", text, flags=re.M)
    text = re.sub(r"^Iprb_inj .*$", "", text, flags=re.M)
    text = re.sub(r"^(VV\d+) vin 0 dc \{VCM\}.*$",
                  r"\1 vin 0 pulse({VCM-0.05} {VCM+0.05} 0.5u 1n 1n 1.5u 4u)", text, flags=re.M)
    return re.sub(r"\.control.*", tran_control(), text, flags=re.S)


def patch_tran_open(text, subckt):
    """Unity-gain follower: tie vm to vout, drive vp with a +/-50 mV step around VCM."""
    hdr = re.search(r"^\.subckt %s (.*)$" % subckt, text, flags=re.M).group(1).split()
    xi = re.search(r"^(XI1 )(.*) %s\s*$" % subckt, text, flags=re.M)
    nodes = xi.group(2).split()
    vm, vp = nodes[hdr.index("vm")], nodes[hdr.index("vp")]
    nodes[hdr.index("vm")] = "vout"
    nodes[hdr.index("vp")] = "vin"
    text = text.replace(xi.group(0), "XI1 " + " ".join(nodes) + " " + subckt)
    text = re.sub(r"^VV2 .*$", "VVstep vin 0 pulse({VCM-0.05} {VCM+0.05} 0.5u 1n 1n 1.5u 4u)", text, flags=re.M)
    return re.sub(r"\.control.*", tran_control(), text, flags=re.S)


def patch_ac_open(text):
    ctl = (".control\nset wr_singlescale\nset wr_vecnames\noption numdgt=7\nop\nac dec 50 10 1G\n"
           "let vout_db = db(v(vout))\nlet vout_ph = 180/pi*cph(v(vout))\n"
           "wrdata ac_bode.txt vout_db vout_ph\n.endc\n.end\n")
    return re.sub(r"\.control.*", ctl, text, flags=re.S)


def xing(x, y, level):
    """First downward crossing of `level` (log-x interpolation)."""
    for i in range(len(y) - 1):
        if (y[i] - level) * (y[i + 1] - level) <= 0 and y[i] != y[i + 1]:
            t = (level - y[i]) / (y[i + 1] - y[i])
            return 10 ** (np.log10(x[i]) + t * (np.log10(x[i + 1]) - np.log10(x[i])))
    return None


def interp_log(x, y, xv):
    return float(np.interp(np.log10(xv), np.log10(x), y))


def metrics_bode(f, db, ph):
    ugf = xing(f, db, 0.0)
    ph0 = ph[0]
    out = {"dc_gain_db": float(db[0]), "ugf_hz": ugf}
    if ugf:
        out["phase_margin_deg"] = 180.0 + (interp_log(f, ph, ugf) - ph0)
    f180 = xing(f, ph, ph0 - 180.0)
    if f180:
        out["f180_hz"] = f180
        out["gain_margin_db"] = -interp_log(f, db, f180)
    return out


def fmt_hz(v):
    return "n/a" if v is None else (f"{v/1e6:.1f} MHz" if v >= 1e6 else f"{v/1e3:.1f} kHz")


def plot_bode(f, db, ph, m, title, ylabel, path, label):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7, 6), sharex=True)
    a1.semilogx(f, db, color=BLUE)
    a1.axhline(0, color=GREY, lw=0.9, ls="--")
    a1.set_ylabel(ylabel + " (dB)")
    a1.set_title(title, loc="left", fontsize=11, fontweight="bold")
    a2.semilogx(f, ph, color=ORANGE)
    a2.set_ylabel("Phase (deg)")
    a2.set_xlabel("Frequency (Hz)")
    if m.get("ugf_hz"):
        for a in (a1, a2):
            a.axvline(m["ugf_hz"], color=GREY, lw=0.9, ls=":")
        a2.plot([m["ugf_hz"]], [interp_log(f, ph, m["ugf_hz"])], "o", color=ORANGE, ms=6)
        a1.plot([m["ugf_hz"]], [0], "o", color=BLUE, ms=6)
    lines = [f"DC gain {m['dc_gain_db']:.1f} dB", f"UGF {fmt_hz(m.get('ugf_hz'))}"]
    if "phase_margin_deg" in m:
        lines.append(f"PM {m['phase_margin_deg']:.1f} deg")
    if "gain_margin_db" in m:
        lines.append(f"GM {m['gain_margin_db']:.1f} dB")
    a1.text(0.02, 0.04, "\n".join(lines), transform=a1.transAxes, fontsize=9, va="bottom",
            bbox=dict(boxstyle="round", fc="white", ec="#d1d5db"))
    fig.text(0.01, 0.005, label, fontsize=7, color=GREY)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def tran_metrics(t, vin, vout):
    """Rising-step metrics: step starts at 0.5 us (1 ns edge), high until 2.0 us."""
    mask = (t >= 0.5e-6) & (t <= 2.0e-6)
    tt, vo = t[mask], vout[mask]
    v0 = float(np.mean(vout[(t > 0.3e-6) & (t < 0.49e-6)]))
    v1 = float(np.mean(vout[(t > 1.8e-6) & (t < 1.99e-6)]))
    dv = v1 - v0
    out = {"step_in_mV": 100.0, "v_low": v0, "v_high": v1, "gain_error_mV": 100.0 - dv * 1e3}
    if abs(dv) < 1e-3:
        return out
    ov = (vo.max() - v1) / dv * 100 if dv > 0 else (v1 - vo.min()) / abs(dv) * 100
    out["overshoot_pct"] = max(float(ov), 0.0)
    for name, lo, hi in (("rise_10_90_ns", 0.1, 0.9),):
        i10 = np.argmax((vo - v0) / dv >= lo)
        i90 = np.argmax((vo - v0) / dv >= hi)
        out[name] = float((tt[i90] - tt[i10]) * 1e9)
    inside = np.abs(vo - v1) > 0.02 * abs(dv)
    idx = np.where(inside)[0]
    out["settle_2pct_ns"] = float((tt[idx[-1]] - 0.5e-6) * 1e9) if len(idx) else 0.0
    out["max_slew_V_per_us"] = float(np.max(np.abs(np.diff(vo) / np.diff(tt))) / 1e6)
    return out


def plot_tran(t, vin, vout, m, title, path, label):
    fig, (ax, az) = plt.subplots(1, 2, figsize=(10, 4), gridspec_kw={"width_ratios": [1, 1.2]})
    for a in (ax, az):
        a.plot(t * 1e6, vin * 1e3, color=GREY, lw=1.3, ls="--", label="Input")
        a.plot(t * 1e6, vout * 1e3, color=BLUE, label="Output")
        a.set_xlabel("Time (us)")
    ax.set_ylabel("Voltage (mV)")
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    ax.set_ylim(vout.min() * 1e3 - 25, vout.max() * 1e3 + 25)
    ax.legend(frameon=False, loc="center right", ncol=1)
    az.set_xlim(0.49, 0.49 + 0.12)
    sel = (t >= 0.49e-6) & (t <= 0.61e-6)
    lo, hi = vout[sel].min() * 1e3, vout[sel].max() * 1e3
    pad = 0.08 * (hi - lo)
    az.set_ylim(lo - pad, hi + pad)
    az.set_title("Zoom: rising edge", loc="left", fontsize=10)
    lines = [f"Overshoot {m['overshoot_pct']:.1f} %" if "overshoot_pct" in m else None,
             f"Settling (2%) {m['settle_2pct_ns']:.1f} ns" if "settle_2pct_ns" in m else None,
             f"Rise 10-90% {m['rise_10_90_ns']:.1f} ns" if "rise_10_90_ns" in m else None,
             f"Gain error {m['gain_error_mV']:.2f} mV"]
    az.text(0.97, 0.05, "\n".join(l for l in lines if l), transform=az.transAxes, fontsize=9,
            va="bottom", ha="right", bbox=dict(boxstyle="round", fc="white", ec="#d1d5db"))
    fig.text(0.01, 0.005, label, fontsize=7, color=GREY)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def load(path, ncol):
    d = np.loadtxt(path, skiprows=1)
    return d


summary = {}
LABEL = "ngspice 42, SkyWater sky130 models (tt), 27 C, VDD 1.8 V"

for name in STB + OPEN:
    cir = find_cir(name)
    text = open(cir).read()
    rd = os.path.join(os.path.dirname(cir), "results") if INPLACE else os.path.join(OUT, name)
    os.makedirs(rd, exist_ok=True)
    pretty = name.replace("sim_", "")
    res = {}
    # ---- loop gain / AC ----
    wd = os.path.join(OUT, "_work", name, "ac")
    os.makedirs(wd, exist_ok=True)
    if name in STB:
        shutil.copy(cir, wd)
        r = run(wd, os.path.basename(cir))
        d = load(os.path.join(wd, "stb.txt"), 4)
        f, db, ph = d[:, 0], d[:, 1], d[:, 2]
        m = metrics_bode(f, db, ph)
        plot_bode(f, db, ph, m, f"{pretty}: loop gain (stb)", "Loop gain", os.path.join(rd, "stb.png"), LABEL)
        np.savetxt(os.path.join(rd, "stb.csv"), np.c_[f, db, ph], delimiter=",",
                   header="freq_hz,loop_gain_db,loop_phase_deg", comments="")
        res["stb"] = m
    else:
        open(os.path.join(wd, os.path.basename(cir)), "w").write(patch_ac_open(text))
        r = run(wd, os.path.basename(cir))
        d = load(os.path.join(wd, "ac_bode.txt"), 4)
        f, db, ph = d[:, 0], d[:, 1], d[:, 2]
        m = metrics_bode(f, db, ph)
        m.pop("phase_margin_deg", None)
        m.pop("gain_margin_db", None)
        m.pop("f180_hz", None)
        # open-loop single-stage: report phase at UGF instead of loop PM
        if m.get("ugf_hz"):
            m["phase_at_ugf_deg"] = interp_log(f, ph, m["ugf_hz"])
        plot_bode(f, db, ph, m, f"{pretty}: open-loop AC", "Gain", os.path.join(rd, "ac.png"), LABEL)
        np.savetxt(os.path.join(rd, "ac.csv"), np.c_[f, db, ph], delimiter=",",
                   header="freq_hz,gain_db,phase_deg", comments="")
        res["ac"] = m
    # ---- transient ----
    wd = os.path.join(OUT, "_work", name, "tran")
    os.makedirs(wd, exist_ok=True)
    ttext = patch_tran_stb(text) if name in STB else patch_tran_open(
        text, re.search(r"^XI1 .* (\S+)\s*$", text, flags=re.M).group(1))
    open(os.path.join(wd, os.path.basename(cir)), "w").write(ttext)
    r = run(wd, os.path.basename(cir))
    d = load(os.path.join(wd, "tran.txt"), 4)
    t, vin, vout = d[:, 0], d[:, 1], d[:, 2]
    tm = tran_metrics(t, vin, vout)
    plot_tran(t, vin, vout, tm, f"{pretty}: unity-gain step response", os.path.join(rd, "tran.png"), LABEL)
    np.savetxt(os.path.join(rd, "tran.csv"), np.c_[t, vin, vout], delimiter=",",
               header="time_s,vin_v,vout_v", comments="")
    res["tran"] = tm
    json.dump(res, open(os.path.join(rd, "results.json"), "w"), indent=1)
    summary[name] = res
    print(name, json.dumps(res))

json.dump(summary, open(os.path.join(SRC if INPLACE else OUT, "results_summary.json" if INPLACE else "summary.json"), "w"), indent=1)
