"""
Self-Healing CI Agent
Bachelor Thesis — Valentin Jurke

Flow per scenario:
  1. Create a new Git branch
  2. Inject broken scenario file(s) via GitHub API
  3. Wait for CI to fail → read logs
  4. Call local Ollama to generate a fix
  5. Apply fix via GitHub API → push
  6. Wait for CI to turn green
  7. Record metrics
"""

import base64
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

import requests

# ── Configuration — edit these ────────────────────────────────────────────────

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")   # set via env var
GITHUB_OWNER  = "Seraph-V"                        # e.g. "Seraph-V"
GITHUB_REPO   = "selfHealing"
BASE_BRANCH   = "main"

OLLAMA_URL    = "http://localhost:11434"
OLLAMA_MODEL  = "codellama:7b"          # change to :13b when ready
OLLAMA_TEMP   = 0.2

MAX_ATTEMPTS  = 3     # LLM fix attempts per scenario run
CI_POLL_SEC   = 15    # seconds between CI status checks
CI_TIMEOUT    = 300   # max seconds to wait for CI result

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    id: str
    failure_type: str          # functional | syntactic | configurational | architectural
    description: str
    broken_files: dict[str, str]   # repo_path → local scenario file path


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
    """Thin wrapper around the GitHub REST API."""
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
    """Get the HEAD SHA of a branch."""
    data = gh("GET", f"/git/ref/heads/{branch}")
    return data["object"]["sha"]


def create_branch(branch_name: str) -> str:
    """Create a new branch from BASE_BRANCH. Returns branch name."""
    sha = get_sha(BASE_BRANCH)
    try:
        gh("POST", "/git/refs", json={
            "ref": f"refs/heads/{branch_name}",
            "sha": sha,
        })
        print(f"  ✓ Branch created: {branch_name}")
    except RuntimeError as e:
        if "already exists" in str(e):
            print(f"  ~ Branch already exists: {branch_name}")
        else:
            raise
    return branch_name


def delete_branch(branch_name: str):
    """Delete a branch (cleanup after experiment)."""
    try:
        gh("DELETE", f"/git/refs/heads/{branch_name}")
    except RuntimeError:
        pass  # ignore if already deleted


def get_file_sha(path: str, branch: str) -> Optional[str]:
    """Get the blob SHA of a file on a branch (needed for updates)."""
    try:
        data = gh("GET", f"/contents/{path}", params={"ref": branch})
        return data["sha"]
    except RuntimeError:
        return None


def push_file(repo_path: str, content: str, message: str, branch: str):
    """Create or update a file on a branch."""
    encoded = base64.b64encode(content.encode()).decode()
    file_sha = get_file_sha(repo_path, branch)
    payload = {
        "message": message,
        "content": encoded,
        "branch": branch,
    }
    if file_sha:
        payload["sha"] = file_sha
    gh("PUT", f"/contents/{repo_path}", json=payload)


def get_file_content(repo_path: str, branch: str) -> str:
    """Download a file's content from a branch."""
    data = gh("GET", f"/contents/{repo_path}", params={"ref": branch})
    return base64.b64decode(data["content"]).decode()


def wait_for_ci(branch: str, commit_sha: str = None) -> tuple[str, str]:
    """
    Poll until the CI run on `branch` completes.
    Returns (conclusion, logs_text) where conclusion is 'success' | 'failure' | 'timeout'.
    """
    print(f"  ⏳ Waiting for CI on branch '{branch}'...", end="", flush=True)
    deadline = time.time() + CI_TIMEOUT
    last_run_id = None

    while time.time() < deadline:
        time.sleep(CI_POLL_SEC)
        runs = gh("GET", "/actions/runs", params={
            "branch": branch,
            "per_page": 5,
        }).get("workflow_runs", [])

        if not runs:
            print(".", end="", flush=True)
            continue

        run = runs[0]  # most recent
        last_run_id = run["id"]
        status     = run["status"]      # queued | in_progress | completed
        conclusion = run["conclusion"]  # success | failure | None

        if status == "completed":
            print(f" {conclusion}")
            logs = fetch_ci_logs(last_run_id)
            return conclusion, logs

        print(".", end="", flush=True)

    print(" TIMEOUT")
    return "timeout", ""


def fetch_ci_logs(run_id: int) -> str:
    """Fetch and return the combined log text of all jobs in a run."""
    jobs = gh("GET", f"/actions/runs/{run_id}/jobs").get("jobs", [])
    log_parts = []
    for job in jobs:
        log_parts.append(f"=== JOB: {job['name']} [{job['conclusion']}] ===")
        for step in job.get("steps", []):
            if step.get("conclusion") == "failure":
                log_parts.append(f"  FAILED STEP: {step['name']}")
    # Full logs need a separate endpoint (returns zip — simplified here)
    return "\n".join(log_parts)


# ── Ollama helpers ────────────────────────────────────────────────────────────

def ollama_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def ollama_generate(prompt: str) -> str:
    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": OLLAMA_TEMP, "num_predict": 2048},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def build_prompt(failure_type: str, ci_logs: str, broken_files: dict[str, str]) -> str:
    files_block = "\n\n".join(
        f"### FILE: {path}\n```python\n{content}\n```"
        for path, content in broken_files.items()
    )
    return f"""You are an expert DevOps engineer. A CI/CD pipeline has failed.
Produce a minimal, correct fix. Respond ONLY with a valid JSON object — no prose, no markdown.

FAILURE TYPE: {failure_type}

CI LOG:
---
{ci_logs[:2000]}
---

BROKEN FILES:
{files_block}

JSON schema to follow exactly:
{{
  "analysis": "<one sentence root-cause>",
  "confidence": <0.0-1.0>,
  "patches": [
    {{
      "filename": "<exact path from above>",
      "original_snippet": "<exact lines to replace>",
      "fixed_snippet": "<replacement>"
    }}
  ],
  "hallucination_risk": "<low|medium|high>",
  "requires_human_review": <true|false>
}}

Only JSON. No other text."""


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
    """Run one scenario, up to MAX_ATTEMPTS fix attempts. Returns list of RunResult."""
    results = []
    branch = f"experiment/{scenario.id}-run{run_index}"
    t_start = time.time()

    print(f"\n{'─'*60}")
    print(f"Scenario : {scenario.id}  [{scenario.failure_type}]  run={run_index}")
    print(f"Branch   : {branch}")

    # 1. Create branch
    create_branch(branch)

    # 2. Read healthy files from main (for context + later restoration)
    healthy_files = {}
    for repo_path in scenario.broken_files.keys():
        try:
            healthy_files[repo_path] = get_file_content(repo_path, BASE_BRANCH)
        except Exception:
            healthy_files[repo_path] = ""

    # 3. Inject broken files
    for repo_path, local_path in scenario.broken_files.items():
        with open(local_path) as f:
            broken_content = f.read()
        push_file(repo_path, broken_content,
                  f"[experiment] inject {scenario.failure_type} regression", branch)
        print(f"  ✓ Injected broken file: {repo_path}")

    # 4. Wait for CI to fail
    conclusion, ci_logs = wait_for_ci(branch)
    if conclusion == "success":
        print("  ⚠  CI passed even with broken files — check scenario definition")
        delete_branch(branch)
        return results

    print(f"  ✗ CI failed as expected ({conclusion})")

    # 5. Read broken file contents for LLM context
    broken_contents = {}
    for repo_path in scenario.broken_files.keys():
        broken_contents[repo_path] = get_file_content(repo_path, branch)

    # 6. Attempt fixes
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  [Attempt {attempt}/{MAX_ATTEMPTS}]")
        result = RunResult(
            scenario_id=scenario.id,
            attempt=attempt,
            branch=branch,
        )

        # LLM call
        prompt = build_prompt(scenario.failure_type, ci_logs, broken_contents)
        print("  🤖 Calling Ollama...", end="", flush=True)
        try:
            raw = ollama_generate(prompt)
            print(" done")
        except Exception as e:
            result.error = f"Ollama error: {e}"
            print(f" ERROR: {e}")
            results.append(result)
            break

        result.llm_raw = raw
        result.patch_generated = bool(raw.strip())

        patch_data = parse_patch(raw)
        if not patch_data:
            result.error = "Could not parse JSON from LLM response"
            print("  ✗ JSON parse failed")
            results.append(result)
            continue

        print(f"  → Analysis : {patch_data.get('analysis', 'N/A')}")
        print(f"  → Confidence: {patch_data.get('confidence', '?')}")

        # Hallucination check
        known = set(scenario.broken_files.keys())
        for p in patch_data.get("patches", []):
            if p.get("filename") not in known:
                result.hallucination = True
                result.error = f"Hallucination: unknown file '{p.get('filename')}'"
                print(f"  ⚠  Hallucination detected: {result.error}")
                break

        if result.hallucination:
            results.append(result)
            continue

        # Apply patches via GitHub API
        diff_lines = []
        apply_ok = True
        for patch in patch_data.get("patches", []):
            repo_path = patch.get("filename")
            original  = patch.get("original_snippet", "")
            fixed     = patch.get("fixed_snippet", "")

            current = get_file_content(repo_path, branch)
            if original not in current:
                result.error = f"Snippet not found in {repo_path} — possible hallucination"
                print(f"  ✗ Snippet not found in {repo_path}")
                apply_ok = False
                break

            patched = current.replace(original, fixed, 1)
            push_file(repo_path, patched,
                      f"[agent] fix attempt {attempt}: {patch_data.get('analysis','')[:60]}",
                      branch)
            diff_lines.append(f"Patched {repo_path}")
            print(f"  ✓ Applied patch to {repo_path}")

        if not apply_ok:
            results.append(result)
            continue

        result.patch_applied = True
        result.patch_diff = "\n".join(diff_lines)

        # Wait for CI
        conclusion, ci_logs = wait_for_ci(branch)
        result.ci_green = conclusion == "success"

        if result.ci_green:
            result.time_to_recovery = time.time() - t_start
            print(f"  ✅ Pipeline GREEN after {result.time_to_recovery:.1f}s")
            results.append(result)
            break
        else:
            print(f"  ✗ Pipeline still failing (attempt {attempt})")
            # Re-read current files for next attempt
            for repo_path in scenario.broken_files.keys():
                broken_contents[repo_path] = get_file_content(repo_path, branch)
            results.append(result)

    # Cleanup: delete experiment branch after run
    # delete_branch(branch)  # comment out if you want to inspect branches on GitHub

    return results


# ── Scenario definitions ──────────────────────────────────────────────────────

def get_scenarios() -> list[Scenario]:
    base = "scenarios"   # local folder with broken files
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
        ),
    ]


# ── Experiment runner ─────────────────────────────────────────────────────────

def run_experiment(runs_per_scenario: int = 3):
    if not GITHUB_TOKEN:
        raise SystemExit("ERROR: Set GITHUB_TOKEN environment variable first.")
    if not ollama_available():
        raise SystemExit("ERROR: Ollama not running. Start with: ollama serve")

    scenarios = get_scenarios()
    all_results: list[RunResult] = []

    print(f"\n{'═'*60}")
    print(f"  THESIS EXPERIMENT")
    print(f"  {len(scenarios)} scenarios × {runs_per_scenario} runs × {MAX_ATTEMPTS} attempts")
    print(f"  Model: {OLLAMA_MODEL}")
    print(f"{'═'*60}")

    for scenario in scenarios:
        for run_idx in range(1, runs_per_scenario + 1):
            results = heal_scenario(scenario, run_idx)
            all_results.extend(results)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print("  RESULTS SUMMARY")
    print(f"{'─'*60}")
    print(f"{'Scenario':<35} {'FSR':>5} {'VPR':>5} {'HR':>5} {'TTR':>7}")
    print(f"{'─'*60}")

    for scenario in scenarios:
        s_results = [r for r in all_results if r.scenario_id == scenario.id]
        total     = len(s_results)
        if total == 0:
            continue
        fsr = sum(1 for r in s_results if r.ci_green) / runs_per_scenario
        vpr = sum(1 for r in s_results if r.patch_applied) / total
        hr  = sum(1 for r in s_results if r.hallucination) / total
        ttr = [r.time_to_recovery for r in s_results if r.time_to_recovery]
        ttr_avg = f"{sum(ttr)/len(ttr):.0f}s" if ttr else "N/A"
        print(f"{scenario.id:<35} {fsr:>4.0%}  {vpr:>4.0%}  {hr:>4.0%}  {ttr_avg:>6}")

    print(f"{'─'*60}")
    print("FSR=Fix Success Rate | VPR=Valid Patch Rate | HR=Hallucination Rate | TTR=Time to Recovery")

    # Save full results
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.makedirs("results", exist_ok=True)
    out = f"results/experiment_{ts}.json"
    with open(out, "w") as f:
        json.dump([asdict(r) for r in all_results], f, indent=2, default=str)
    print(f"\nFull results → {out}")


if __name__ == "__main__":
    run_experiment(runs_per_scenario=3)
