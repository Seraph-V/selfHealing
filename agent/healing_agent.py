"""
Self-Healing CI Agent — v2 (bugfixed)
Bachelor Thesis — Valentin Jurke

Fixes vs v1:
  FIX 1 (functional)  : Prompt verlangt jetzt vollständige Funktion als Snippet → eindeutig
  FIX 2 (syntactic)   : Whitespace-normalisiertes Snippet-Matching
  FIX 3 (architectural): context_files zeigt LLM was in src/data.py existiert;
                         Prompt fordert Fixes für ALLE betroffenen Dateien
"""

import base64
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER  = "Seraph-V"
GITHUB_REPO   = "selfHealing"
BASE_BRANCH   = "main"

OLLAMA_URL    = "http://localhost:11434"
OLLAMA_MODEL  = "codellama:7b"
OLLAMA_TEMP   = 0.2

MAX_ATTEMPTS  = 3
CI_POLL_SEC   = 15
CI_TIMEOUT    = 300

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    id: str
    failure_type: str
    description: str
    broken_files: dict[str, str]       # repo_path → local broken file
    context_files: list[str] = field(default_factory=list)  # FIX 3: extra read-only context


@dataclass
class RunResult:
    scenario_id: str
    attempt: int
    branch: str
    patch_generated: bool = False
    patch_applied: bool   = False
    ci_green: bool        = False
    hallucination: bool   = False
    time_to_recovery: Optional[float] = None
    llm_raw: str = ""
    patch_diff: str = ""
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ── GitHub API helpers ────────────────────────────────────────────────────────

def gh(method: str, path: str, **kwargs):
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}{path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub API {method} {path} → {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.text else {}


def get_sha(branch: str) -> str:
    return gh("GET", f"/git/ref/heads/{branch}")["object"]["sha"]


def create_branch(branch_name: str) -> str:
    sha = get_sha(BASE_BRANCH)
    try:
        gh("POST", "/git/refs", json={"ref": f"refs/heads/{branch_name}", "sha": sha})
        print(f"  ✓ Branch created: {branch_name}")
    except RuntimeError as e:
        if "already exists" in str(e):
            print(f"  ~ Branch already exists: {branch_name}")
        else:
            raise
    return branch_name


def delete_branch(branch_name: str):
    try:
        gh("DELETE", f"/git/refs/heads/{branch_name}")
    except RuntimeError:
        pass


def get_file_sha(path: str, branch: str) -> Optional[str]:
    try:
        return gh("GET", f"/contents/{path}", params={"ref": branch})["sha"]
    except RuntimeError:
        return None


def push_file(repo_path: str, content: str, message: str, branch: str):
    encoded = base64.b64encode(content.encode()).decode()
    file_sha = get_file_sha(repo_path, branch)
    payload = {"message": message, "content": encoded, "branch": branch}
    if file_sha:
        payload["sha"] = file_sha
    gh("PUT", f"/contents/{repo_path}", json=payload)


def get_file_content(repo_path: str, branch: str) -> str:
    data = gh("GET", f"/contents/{repo_path}", params={"ref": branch})
    return base64.b64decode(data["content"]).decode()


def wait_for_ci(branch: str) -> tuple[str, str]:
    print(f"  ⏳ Waiting for CI on '{branch}'...", end="", flush=True)
    deadline = time.time() + CI_TIMEOUT
    seen_run_id = None

    while time.time() < deadline:
        time.sleep(CI_POLL_SEC)
        runs = gh("GET", "/actions/runs", params={"branch": branch, "per_page": 5}).get("workflow_runs", [])
        if not runs:
            print(".", end="", flush=True)
            continue

        run = runs[0]
        if run["status"] == "completed":
            print(f" {run['conclusion']}")
            return run["conclusion"], fetch_ci_logs(run["id"])
        print(".", end="", flush=True)

    print(" TIMEOUT")
    return "timeout", ""


def fetch_ci_logs(run_id: int) -> str:
    jobs = gh("GET", f"/actions/runs/{run_id}/jobs").get("jobs", [])
    parts = []
    for job in jobs:
        parts.append(f"=== JOB: {job['name']} [{job['conclusion']}] ===")
        for step in job.get("steps", []):
            if step.get("conclusion") == "failure":
                parts.append(f"  FAILED STEP: {step['name']}")
    return "\n".join(parts)


# ── Ollama helpers ────────────────────────────────────────────────────────────

def ollama_available() -> bool:
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).status_code == 200
    except Exception:
        return False


def ollama_generate(prompt: str) -> str:
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
              "options": {"temperature": OLLAMA_TEMP, "num_predict": 2048}},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


# ── FIX 1 + FIX 3: Verbesserter Prompt ───────────────────────────────────────

def build_prompt(failure_type: str, ci_logs: str,
                 broken_files: dict[str, str],
                 context_files: dict[str, str] = None) -> str:

    # Broken files block
    files_block = "\n\n".join(
        f"### FILE TO FIX: {path}\n```python\n{content}\n```"
        for path, content in broken_files.items()
    )

    # FIX 3: context files block (read-only, e.g. src/data.py)
    context_block = ""
    if context_files:
        context_block = "\n\nCONTEXT FILES (do NOT patch these, use them as reference):\n"
        context_block += "\n\n".join(
            f"### CONTEXT: {path}\n```python\n{content}\n```"
            for path, content in context_files.items()
        )

    # FIX 1: Architectural extra instruction
    arch_note = ""
    if failure_type == "architectural":
        arch_note = """
ARCHITECTURAL NOTE:
- Fix ALL files that contribute to the circular dependency, not just one.
- Only import symbols that actually exist in the referenced module (check CONTEXT FILES).
- The correct fix is to remove the cross-service import and use the shared data layer instead.
"""

    return f"""You are an expert DevOps engineer fixing a CI/CD pipeline failure.
Respond ONLY with a valid JSON object. No prose, no markdown fences.

FAILURE TYPE: {failure_type}

CI LOG:
---
{ci_logs[:2000]}
---

{files_block}{context_block}{arch_note}

RULES FOR PATCHES:
1. original_snippet MUST include the complete function or class block — never just a single line.
   This guarantees the snippet is unique in the file and can be matched exactly.
2. fixed_snippet replaces original_snippet entirely. Keep indentation consistent.
3. Only patch FILES TO FIX listed above. Never patch CONTEXT FILES.
4. Only reference symbols that actually exist in the codebase (check CONTEXT FILES).

Respond with ONLY this JSON — no other text:
{{
  "analysis": "<one sentence root-cause>",
  "confidence": <0.0-1.0>,
  "patches": [
    {{
      "filename": "<exact path>",
      "original_snippet": "<complete function/block — must be unique in the file>",
      "fixed_snippet": "<replacement>"
    }}
  ],
  "hallucination_risk": "<low|medium|high>",
  "requires_human_review": <true|false>
}}"""


# ── FIX 2: Whitespace-normalisiertes Snippet-Matching ────────────────────────

def normalize_ws(text: str) -> str:
    """Entfernt trailing whitespace pro Zeile — löst Syntactic-Mismatch."""
    return "\n".join(line.rstrip() for line in text.split("\n"))


def find_snippet(content: str, snippet: str) -> bool:
    """Prüft ob snippet im content steht — mit und ohne Whitespace-Normalisierung."""
    if snippet in content:
        return True
    # FIX 2: Fallback mit normalisiertem Vergleich
    return normalize_ws(snippet) in normalize_ws(content)


def apply_snippet(content: str, original: str, replacement: str) -> str:
    """Ersetzt original durch replacement — normalisiert bei Bedarf."""
    if original in content:
        return content.replace(original, replacement, 1)
    # FIX 2: Normalisierter Fallback
    norm_content  = normalize_ws(content)
    norm_original = normalize_ws(original)
    norm_replace  = normalize_ws(replacement)
    return norm_content.replace(norm_original, norm_replace, 1)


def parse_patch(raw: str) -> Optional[dict]:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        s, e = clean.find("{"), clean.rfind("}") + 1
        if s != -1 and e > s:
            try:
                return json.loads(clean[s:e])
            except json.JSONDecodeError:
                pass
    return None


# ── Healing loop ──────────────────────────────────────────────────────────────

def heal_scenario(scenario: Scenario, run_index: int) -> list[RunResult]:
    results = []
    branch  = f"experiment/{scenario.id}-run{run_index}"
    t_start = time.time()

    print(f"\n{'─'*60}")
    print(f"Scenario : {scenario.id}  [{scenario.failure_type}]  run={run_index}")
    print(f"Branch   : {branch}")

    create_branch(branch)

    # Inject broken files
    for repo_path, local_path in scenario.broken_files.items():
        with open(local_path) as f:
            broken_content = f.read()
        push_file(repo_path, broken_content,
                  f"[experiment] inject {scenario.failure_type} regression", branch)
        print(f"  ✓ Injected: {repo_path}")

    # Wait for CI to fail
    conclusion, ci_logs = wait_for_ci(branch)
    if conclusion == "success":
        print("  ⚠  CI passed with broken files — check scenario definition")
        return results

    print(f"  ✗ CI failed as expected")

    # Read broken file contents + FIX 3: context files from main
    broken_contents: dict[str, str] = {}
    for repo_path in scenario.broken_files.keys():
        broken_contents[repo_path] = get_file_content(repo_path, branch)

    context_contents: dict[str, str] = {}
    for repo_path in scenario.context_files:
        try:
            context_contents[repo_path] = get_file_content(repo_path, BASE_BRANCH)
        except Exception:
            pass

    # Attempt fixes
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  [Attempt {attempt}/{MAX_ATTEMPTS}]")
        result = RunResult(scenario_id=scenario.id, attempt=attempt, branch=branch)

        # LLM call
        prompt = build_prompt(
            scenario.failure_type, ci_logs, broken_contents, context_contents
        )
        print("  Calling Ollama...", end="", flush=True)
        try:
            raw = ollama_generate(prompt)
            print(" done")
        except Exception as e:
            result.error = f"Ollama error: {e}"
            results.append(result)
            break

        result.llm_raw        = raw
        result.patch_generated = bool(raw.strip())

        patch_data = parse_patch(raw)
        if not patch_data:
            result.error = "Could not parse JSON from LLM response"
            print("  ✗ JSON parse failed")
            results.append(result)
            continue

        print(f"  → {patch_data.get('analysis', 'N/A')}  (confidence={patch_data.get('confidence','?')})")

        # Hallucination check
        known = set(scenario.broken_files.keys())
        for p in patch_data.get("patches", []):
            if p.get("filename") not in known:
                result.hallucination = True
                result.error = f"Hallucination: unknown file '{p.get('filename')}'"
                print(f"  ⚠  {result.error}")
                break

        if result.hallucination:
            results.append(result)
            continue

        # Apply patches (FIX 1 + FIX 2)
        diff_lines = []
        apply_ok   = True
        for patch in patch_data.get("patches", []):
            repo_path = patch.get("filename")
            original  = patch.get("original_snippet", "")
            fixed     = patch.get("fixed_snippet", "")

            current = get_file_content(repo_path, branch)

            if not find_snippet(current, original):
                result.error = f"Snippet not found in {repo_path}"
                print(f"  ✗ {result.error}")
                apply_ok = False
                break

            patched = apply_snippet(current, original, fixed)
            push_file(repo_path, patched,
                      f"[agent] fix attempt {attempt}: {patch_data.get('analysis','')[:60]}",
                      branch)
            # Update local copy for next iteration
            broken_contents[repo_path] = patched
            diff_lines.append(f"Patched {repo_path}")
            print(f"  ✓ Applied patch to {repo_path}")

        if not apply_ok:
            results.append(result)
            continue

        result.patch_applied = True
        result.patch_diff    = "\n".join(diff_lines)

        conclusion, ci_logs = wait_for_ci(branch)
        result.ci_green = conclusion == "success"

        if result.ci_green:
            result.time_to_recovery = time.time() - t_start
            print(f"  ✅ Pipeline GREEN after {result.time_to_recovery:.1f}s")
            results.append(result)
            break
        else:
            print(f"  ✗ Still failing (attempt {attempt})")
            results.append(result)

    return results


# ── Scenario definitions ──────────────────────────────────────────────────────

def get_scenarios() -> list[Scenario]:
    base = "scenarios"
    return [
        Scenario(
            id="functional_regression",
            failure_type="functional",
            description="Wrong arithmetic operators in calculator.py",
            broken_files={"src/calculator.py": f"{base}/functional_regression.py"},
        ),
        Scenario(
            id="syntactic_regression",
            failure_type="syntactic",
            description="PEP8 violations in utils.py",
            broken_files={"src/utils.py": f"{base}/syntactic_regression.py"},
        ),
        Scenario(
            id="configurational_regression",
            failure_type="configurational",
            description="Nonexistent package version in requirements.txt",
            broken_files={"requirements.txt": f"{base}/configurational_regression.txt"},
        ),
        Scenario(
            id="architectural_regression",
            failure_type="architectural",
            description="Circular import between user.py and order.py",
            broken_files={
                "src/services/user.py":  f"{base}/architectural_regression_user.py",
                "src/services/order.py": f"{base}/architectural_regression_order.py",
            },
            # FIX 3: LLM sieht was in src/data.py wirklich existiert
            context_files=["src/data.py"],
        ),
    ]


# ── Experiment runner ─────────────────────────────────────────────────────────

def run_experiment(runs_per_scenario: int = 3):
    if not GITHUB_TOKEN:
        raise SystemExit("ERROR: Set GITHUB_TOKEN environment variable first.")
    if not ollama_available():
        raise SystemExit("ERROR: Ollama not running. Start with: ollama serve")

    scenarios    = get_scenarios()
    all_results: list[RunResult] = []

    print(f"\n{'═'*60}")
    print(f"  THESIS EXPERIMENT — v2")
    print(f"  {len(scenarios)} scenarios × {runs_per_scenario} runs × {MAX_ATTEMPTS} attempts")
    print(f"  Model: {OLLAMA_MODEL}")
    print(f"{'═'*60}")

    for scenario in scenarios:
        for run_idx in range(1, runs_per_scenario + 1):
            results = heal_scenario(scenario, run_idx)
            all_results.extend(results)

    # Summary
    print(f"\n{'═'*60}")
    print("  RESULTS SUMMARY")
    print(f"{'─'*60}")
    print(f"{'Scenario':<35} {'FSR':>5} {'VPR':>5} {'HR':>5} {'TTR':>7}")
    print(f"{'─'*60}")

    for scenario in scenarios:
        s_res = [r for r in all_results if r.scenario_id == scenario.id]
        total = len(s_res)
        if not total:
            continue
        fsr = sum(1 for r in s_res if r.ci_green) / runs_per_scenario
        vpr = sum(1 for r in s_res if r.patch_applied) / total
        hr  = sum(1 for r in s_res if r.hallucination) / total
        ttr = [r.time_to_recovery for r in s_res if r.time_to_recovery]
        ttr_avg = f"{sum(ttr)/len(ttr):.0f}s" if ttr else "N/A"
        print(f"{scenario.id:<35} {fsr:>4.0%}  {vpr:>4.0%}  {hr:>4.0%}  {ttr_avg:>6}")

    print(f"{'─'*60}")
    print("FSR | VPR | HR | TTR")

    ts  = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.makedirs("results", exist_ok=True)
    out = f"results/experiment_{ts}.json"
    with open(out, "w") as f:
        json.dump([asdict(r) for r in all_results], f, indent=2, default=str)
    print(f"\nFull results → {out}")


if __name__ == "__main__":
    run_experiment(runs_per_scenario=3)
