import glob
import json
import os
import sys
from collections import defaultdict


def latest_results_file() -> str:
    files = glob.glob(os.path.join(os.path.dirname(__file__), "..", "results", "experiment_*.json"))
    if not files:
        raise SystemExit("No results/experiment_*.json files found.")
    return max(files, key=os.path.getmtime)


def summarize(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for r in data:
        by_scenario[r["scenario_id"]].append(r)

    rows = []
    for sid, attempts in by_scenario.items():
        branches = {a["branch"] for a in attempts}
        runs = len(branches)
        total = len(attempts)
        applied = sum(1 for a in attempts if a["patch_applied"])
        green = sum(1 for a in attempts if a["ci_green"])
        ttrs = [a["time_to_recovery"] for a in attempts if a["time_to_recovery"]]

        rows.append({
            "scenario_id": sid,
            "runs": runs,
            "attempts": total,
            "applied": applied,
            "green": green,
            "fsr": green / runs if runs else 0.0,
            "vpr": applied / total if total else 0.0,
            "mttr": sum(ttrs) / len(ttrs) if ttrs else None,
        })

    rows.sort(key=lambda r: r["scenario_id"])
    return rows


def print_table(rows: list[dict], path: str) -> None:
    print(f"\nSummary for {path}\n")
    header = f"{'Scenario':<32}{'Runs':>5}{'FSR':>7}{'VPR':>7}{'MTTR':>9}"
    print(header)
    print("-" * len(header))

    tot_runs = tot_attempts = tot_applied = tot_green = 0
    for r in rows:
        mttr = f"{r['mttr']:.0f}s" if r["mttr"] else "N/A"
        print(f"{r['scenario_id']:<32}{r['runs']:>5}{r['fsr']:>7.0%}{r['vpr']:>7.0%}{mttr:>9}")
        tot_runs += r["runs"]
        tot_attempts += r["attempts"]
        tot_applied += r["applied"]
        tot_green += r["green"]

    print("-" * len(header))
    overall_fsr = tot_green / tot_runs if tot_runs else 0.0
    overall_vpr = tot_applied / tot_attempts if tot_attempts else 0.0
    print(f"{'TOTAL':<32}{tot_runs:>5}{overall_fsr:>7.0%}{overall_vpr:>7.0%}")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else latest_results_file()
    rows = summarize(path)
    print_table(rows, path)

    out_path = path.rsplit(".json", 1)[0] + "_summary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
