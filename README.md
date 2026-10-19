# AMPICBench

**A collection of analog circuits for benchmarking sizing, simulation, and layout tools.**

AMPICBench is maintained by the AMPIC group. It provides ready-to-simulate
ngspice netlists of analog building blocks in the open-source SkyWater
**sky130A** process, together with per-corner operating-region data, so that
automated analog design flows (including LLM-based ones) can be tested against
the same reference circuits.

## Contents

```
AMPICBench/
├── Bias_Circuits/
│   ├── sim_Bias_gen/                          Simple current-reference bias generator
│   └── sim_Cascode_bias/                      Wide-swing cascode bias network
├── Opamps/
│   ├── sim_Diffamp1/                          Differential amplifier (open-loop AC)
│   ├── sim_Telescopic_Amp/                    Telescopic cascode amplifier (open-loop AC)
│   ├── sim_FC_Amp/                            Folded-cascode amplifier (open-loop AC)
│   ├── sim_Opamp1_stb/                        Opamp1 with loop-gain / stability analysis
│   ├── sim_Opamp_2stg_Diff_ClassA_STB/        Two-stage: diff-pair first stage + class-A second stage
│   ├── sim_Opamp_2stg_FC_ClassA_STB/          Two-stage: folded-cascode first stage + class-A second stage
│   └── sim_Opamp_2stg_Telescopic_ClassA_STB/  Two-stage: telescopic first stage + class-A second stage
└── Sky130_char/
    ├── nmos_char/                             sky130 LVT NMOS operating-point / DC sweep characterization
    └── pmos_char/                             sky130 LVT PMOS operating-point / DC sweep characterization
```

Each circuit folder contains:

| File | Purpose |
|------|---------|
| `sim_<name>.cir` | Top-level ngspice netlist: parameters, subcircuits, testbench, and `.control` block |
| `sim_<name>_cells.spice` | The same subcircuits with all parameters evaluated to plain numbers (W/L in um) |
| `corners.json` | Per-corner (tt/ff/ss/fs/sf at -40, 27, 125 C) worst saturation margin in mV and the list of devices in triode |
| `.spiceinit` | ngspice settings needed for the sky130 models |

The `Sky130_char` folders contain only the `.cir` and `.spiceinit` files.

## Analyses included

- **Bias circuits and device characterization:** DC sweep (`dc VV1 0 1.8 0.036`) plus operating-point dump.
- **Open-loop amplifiers (Diffamp1, Telescopic, FC):** operating point and AC sweep, 10 Hz to 1 GHz.
- **Opamps with `_stb`:** operating point and loop-gain AC sweep, 10 Hz to 10 GHz, using Tian
  double-injection (the ngspice equivalent of Spectre `stb`). The control block measures unity-gain
  frequency, phase margin, DC gain, and gain margin.

Every simulation writes `op_nodes.txt` and `op_devices.txt` (id, vgs, vds, vth, vdsat, gm, gds, cgs, cgd).

## Requirements

- [ngspice](https://ngspice.sourceforge.io/)
- The [SkyWater sky130A PDK](https://github.com/google/skywater-pdk), for example installed via
  [open_pdks](https://github.com/RTimothyEdwards/open_pdks)

## Running a simulation

The netlists reference the PDK model library with an absolute path:

```
.lib "/opt/pdks/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
```

Change this line to point at your own PDK install (or symlink your PDK to `/opt/pdks`).
Then run from inside a circuit folder so that `.spiceinit` is picked up:

```bash
cd Opamps/sim_FC_Amp
ngspice -b sim_FC_Amp.cir
```

To change the process corner, edit the corner name at the end of the `.lib` line
(`tt`, `ff`, `ss`, `fs`, `sf`) and the `.temp` value.

## Provenance

The netlists were converted from Cadence ADE Spectre testbenches (sky130 kit) to ngspice.
Device area and perimeter parameters (`as`, `ad`, `ps`, `pd`) reproduce the Cadence pcell geometry via
the `cds_*` functions in each netlist. Design changes relative to the original Cadence designs are
recorded as `DESIGN CHANGE` comments at the top of the affected `.cir` files.

## Citing

If you use AMPICBench in your work, please cite it. GitHub shows a "Cite this repository" button
generated from `CITATION.cff`. BibTeX:

```bibtex
@misc{ampicbench2026,
  title        = {{AMPICBench}: Analog Circuit Benchmarks for Sizing, Simulation, and Layout Tools},
  author       = {Saxena, Vishal},
  organization = {{AMPIC Group}},
  year         = {2026},
  howpublished = {\url{https://github.com/<your-user-or-org>/ampic-bench}},
  note         = {Version 0.1.0}
}
```

A reference to the accompanying paper will be added here once it is available.

## License

Copyright (c) 2026 Vishal Saxena, AMPIC Group.

AMPICBench is released under the
[Creative Commons Attribution-NonCommercial 4.0 International](https://creativecommons.org/licenses/by-nc/4.0/)
license (CC BY-NC 4.0); see [LICENSE](LICENSE). You may share and adapt the material for
non-commercial purposes with attribution. For commercial use, please contact the AMPIC group.

The sky130 PDK and models are not part of this repository and remain under their own license (Apache-2.0).
