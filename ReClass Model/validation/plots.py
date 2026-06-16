"""Diagnostic plotting for validation reports.

Turns the JSON reports written by ``validation/harness.py`` into PNG figures so
the engine's progress can be evaluated visually. Plots are written to a top-level
``plots/`` folder at the project root.

Generated per benchmark (e.g. ``synthetic_v1``):
  * ``<bench>_confusion_matrix.png`` - expected vs predicted tier heatmap.
  * ``<bench>_tier_distribution.png`` - expected vs predicted tier counts.
  * ``<bench>_concordance.png``       - definitive/overall concordance vs gate bar.
  * ``<bench>_ancestry_concordance.png`` - concordance per ancestry (if >1 group).

And one cross-benchmark figure when several reports exist:
  * ``summary_concordance.png`` - definitive concordance per benchmark vs the gate.

Usage:
    python validation/plots.py                 # rebuild plots from all reports
    python validation/plots.py synthetic_v1    # plots for one benchmark's report

This module is import-safe: ``harness.py`` calls :func:`generate_for_report` after
each run, guarded so a missing matplotlib only prints a hint instead of failing.
"""

from __future__ import annotations

import glob
import json
import os

import matplotlib

matplotlib.use("Agg")  # headless: render straight to PNG, no display needed
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(HERE, "reports")
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))  # parent of "ReClass Model"
PLOTS_DIR = os.path.join(PROJECT_ROOT, "plots")

TIERS = ["Benign", "Likely Benign", "VUS", "Likely Pathogenic", "Pathogenic"]
_SHORT = {
    "Benign": "B",
    "Likely Benign": "LB",
    "VUS": "VUS",
    "Likely Pathogenic": "LP",
    "Pathogenic": "P",
}
GATE_BAR = 0.85


def _ensure_dir() -> None:
    os.makedirs(PLOTS_DIR, exist_ok=True)


def _confusion(cases: list) -> list:
    idx = {t: i for i, t in enumerate(TIERS)}
    matrix = [[0] * len(TIERS) for _ in TIERS]
    for c in cases:
        e, p = c.get("expected"), c.get("predicted")
        if e in idx and p in idx:
            matrix[idx[e]][idx[p]] += 1
    return matrix


def _plot_confusion(bench: str, cases: list) -> str:
    matrix = _confusion(cases)
    row_tot = [sum(r) or 1 for r in matrix]
    frac = [[matrix[i][j] / row_tot[i] for j in range(len(TIERS))] for i in range(len(TIERS))]

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(frac, cmap="Blues", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(TIERS)))
    ax.set_yticks(range(len(TIERS)))
    ax.set_xticklabels([_SHORT[t] for t in TIERS])
    ax.set_yticklabels(TIERS)
    ax.set_xlabel("Predicted tier")
    ax.set_ylabel("Expected tier")
    ax.set_title(f"Confusion matrix - {bench}\n(cell color = row fraction, label = count)")
    for i in range(len(TIERS)):
        for j in range(len(TIERS)):
            count = matrix[i][j]
            if count:
                ax.text(j, i, str(count), ha="center", va="center",
                        color="white" if frac[i][j] > 0.5 else "black", fontsize=9)
    # highlight the diagonal (correct calls)
    for k in range(len(TIERS)):
        ax.add_patch(plt.Rectangle((k - 0.5, k - 0.5), 1, 1, fill=False,
                                   edgecolor="#2ca02c", lw=2))
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="fraction of expected row")
    fig.tight_layout()
    return _save(fig, f"{bench}_confusion_matrix.png")


def _plot_tier_distribution(bench: str, cases: list) -> str:
    exp = [0] * len(TIERS)
    pred = [0] * len(TIERS)
    idx = {t: i for i, t in enumerate(TIERS)}
    for c in cases:
        if c.get("expected") in idx:
            exp[idx[c["expected"]]] += 1
        if c.get("predicted") in idx:
            pred[idx[c["predicted"]]] += 1

    x = range(len(TIERS))
    w = 0.4
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([i - w / 2 for i in x], exp, width=w, label="Expected", color="#4c78a8")
    ax.bar([i + w / 2 for i in x], pred, width=w, label="Predicted", color="#f58518")
    ax.set_xticks(list(x))
    ax.set_xticklabels(TIERS, rotation=20, ha="right")
    ax.set_ylabel("Number of variants")
    ax.set_title(f"Tier distribution: expected vs predicted - {bench}")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, f"{bench}_tier_distribution.png")


def _plot_concordance(bench: str, metrics: dict, gate_pass: bool) -> str:
    labels = ["Definitive\nconcordance", "Overall\nconcordance", "Serious\ndiscordance"]
    values = [
        metrics.get("definitive_concordance", 0.0),
        metrics.get("overall_concordance", 0.0),
        metrics.get("serious_rate", 0.0),
    ]
    colors = ["#54a24b" if values[0] >= GATE_BAR else "#e45756", "#4c78a8", "#e45756"]

    fig, ax = plt.subplots(figsize=(7, 5))
    bars = ax.bar(labels, [v * 100 for v in values], color=colors)
    ax.axhline(GATE_BAR * 100, ls="--", color="#333", lw=1)
    ax.text(2.45, GATE_BAR * 100 + 1, "gate >= 85%", ha="right", fontsize=8, color="#333")
    ax.set_ylabel("Percent")
    ax.set_ylim(0, 105)
    verdict = "PASS" if gate_pass else "FAIL"
    ax.set_title(f"Concordance & gate - {bench}  (gate: {verdict})")
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                f"{v * 100:.1f}%", ha="center", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, f"{bench}_concordance.png")


def _plot_ancestry(bench: str, metrics: dict) -> str | None:
    """Concordance per group (the `ancestry` field holds VCEP/group for real data).

    With a handful of groups we use vertical bars; with many groups (e.g. the
    ~40 ClinGen VCEPs) we switch to a sorted horizontal layout so every label
    stays readable.
    """
    by = metrics.get("by_ancestry", {})
    if len(by) <= 1:
        return None  # nothing to compare

    items = sorted(by.items(), key=lambda kv: kv[1]["concordance"])
    names = [k for k, _ in items]
    conc = [v["concordance"] * 100 for _, v in items]
    counts = [v["n"] for _, v in items]
    colors = ["#54a24b" if c >= GATE_BAR * 100 else "#e45756" for c in conc]

    if len(names) > 10:
        fig, ax = plt.subplots(figsize=(9, max(4, 0.32 * len(names) + 1.5)))
        ax.barh(names, conc, color=colors)
        ax.axvline(GATE_BAR * 100, ls="--", color="#333", lw=1)
        ax.set_xlabel("Concordance (%)")
        ax.set_xlim(0, 108)
        ax.set_title(f"Concordance by group ({bench}) - sorted; green >= 85% gate")
        for i, (c, n) in enumerate(zip(conc, counts)):
            ax.text(c + 1, i, f"{c:.0f}% (n={n})", va="center", fontsize=7)
        ax.tick_params(axis="y", labelsize=7)
    else:
        fig, ax = plt.subplots(figsize=(8, 5))
        x = list(range(len(names)))
        bars = ax.bar(x, conc, color=colors)
        ax.axhline(GATE_BAR * 100, ls="--", color="#333", lw=1)
        ax.set_ylabel("Concordance (%)")
        ax.set_ylim(0, 105)
        ax.set_title(f"Concordance by group - {bench}")
        for b, n in zip(bars, counts):
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                    f"n={n}", ha="center", fontsize=8)
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=20, ha="right")
    ax.grid(axis="x" if len(names) > 10 else "y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, f"{bench}_ancestry_concordance.png")


def _save(fig, name: str) -> str:
    _ensure_dir()
    path = os.path.join(PLOTS_DIR, name)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def generate_for_report(report: dict) -> list:
    """Generate all per-benchmark plots for one in-memory report payload."""
    bench = report.get("benchmark") or "synthetic_v1"
    metrics = report.get("metrics", {})
    cases = report.get("cases", [])
    written = [
        _plot_confusion(bench, cases),
        _plot_tier_distribution(bench, cases),
        _plot_concordance(bench, metrics, report.get("gate_pass", False)),
    ]
    anc = _plot_ancestry(bench, metrics)
    if anc:
        written.append(anc)
    return written


def generate_for_report_file(path: str) -> list:
    with open(path) as f:
        return generate_for_report(json.load(f))


def generate_summary() -> str | None:
    """Cross-benchmark definitive-concordance comparison from all reports."""
    paths = sorted(
        p for p in glob.glob(os.path.join(REPORTS_DIR, "validation_report*.json"))
        if "failure_analysis" not in os.path.basename(p)
    )
    rows = []
    for p in paths:
        with open(p) as f:
            d = json.load(f)
        m = d.get("metrics", {})
        rows.append((d.get("benchmark") or "synthetic_v1",
                     m.get("definitive_concordance", 0.0),
                     bool(d.get("gate_pass"))))
    if len(rows) < 2:
        return None

    names = [r[0] for r in rows]
    conc = [r[1] * 100 for r in rows]
    colors = ["#54a24b" if r[2] else "#e45756" for r in rows]

    fig, ax = plt.subplots(figsize=(8, 5))
    x = list(range(len(names)))
    bars = ax.bar(x, conc, color=colors)
    ax.axhline(GATE_BAR * 100, ls="--", color="#333", lw=1)
    ax.text(len(names) - 0.5, GATE_BAR * 100 + 1, "gate >= 85%", ha="right",
            fontsize=8, color="#333")
    ax.set_ylabel("Definitive concordance (%)")
    ax.set_ylim(0, 105)
    ax.set_title("Definitive concordance by benchmark (green = gate PASS)")
    for b, v in zip(bars, conc):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
                f"{v:.1f}%", ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _save(fig, "summary_concordance.png")


def main(argv: list | None = None) -> int:
    import sys
    argv = sys.argv[1:] if argv is None else argv
    written: list = []
    if argv:
        path = os.path.join(REPORTS_DIR, f"validation_report"
                            + ("" if argv[0] == "synthetic_v1" else "_" + argv[0]) + ".json")
        if not os.path.exists(path):
            raise SystemExit(f"No report at {path}. Run validation/harness.py {argv[0]} first.")
        written += generate_for_report_file(path)
    else:
        for p in sorted(glob.glob(os.path.join(REPORTS_DIR, "validation_report*.json"))):
            if "failure_analysis" in os.path.basename(p):
                continue
            written += generate_for_report_file(p)
    summary = generate_summary()
    if summary:
        written.append(summary)
    for w in written:
        print(f"Wrote: {os.path.relpath(w, PROJECT_ROOT)}")
    print(f"\n{len(written)} plot(s) in {PLOTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
