"""
Self-Healing CI Agent — v7 (fully autonomous)
Bachelor Thesis — Valentin Jurke

v7 changes:
  - Scenarios have no predefined failure_type; agent classifies from CI logs only
  - Files to fix are scraped from CI logs (regex + LLM fallback); not hardcoded
  - ground_truth_type kept in Scenario for evaluation metrics only, not used by agent
"""

import base64
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER  = "Seraph-V"
GITHUB_REPO   = "selfHealing"
BASE_BRANCH   = os.environ.get("EXPERIMENT_BASE_BRANCH", "main")

OLLAMA_URL    = "http://localhost:11434"
OLLAMA_MODEL  = "qwen2.5-coder:14b"
OLLAMA_TEMP   = 0.2

MAX_ATTEMPTS  = 3
CI_POLL_SEC   = 15
CI_TIMEOUT    = 300

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Scenario:
    id: str
    description: str
    broken_files: dict[str, str]        # repo_path → local broken file (injection only)
    context_files: list[str] = field(default_factory=list)  # repo_paths shown read-only to the LLM
    ground_truth_type: str = ""         # for accuracy metrics only — agent must NOT use this


@dataclass
class RunResult:
    scenario_id: str
    attempt: int
    branch: str
    patch_generated: bool = False
    patch_applied: bool   = False
    ci_green: bool        = False
    time_to_recovery: Optional[float] = None
    llm_raw: str = ""
    patch_diff: str = ""
    error: str = ""
    detected_type: str = ""
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


def get_file_sha(path: str, branch: str, retries: int = 2) -> Optional[str]:
    """
    Returns the current SHA of a file on the branch, or None if the file
    genuinely does not exist (404). A non-404 failure (network blip,
    transient GitHub inconsistency right after branch creation, rate
    limiting) is retried instead of being treated as "file doesn't exist" —
    conflating the two previously caused push_file() to omit the required
    'sha' for a file that does exist, which GitHub rejects with a 422.
    """
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return gh("GET", f"/contents/{path}", params={"ref": branch})["sha"]
        except RuntimeError as e:
            if "→ 404:" in str(e):
                return None
            last_error = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(
        f"get_file_sha: persistent failure for '{path}' after "
        f"{retries + 1} attempts: {last_error}"
    )


def push_file(repo_path: str, content: str, message: str, branch: str, retries: int = 2):
    """
    Pushes a file to the branch. Retries the full get-SHA-then-PUT sequence
    on failure (not just the PUT) so a stale or missing SHA — e.g. from a
    transient error on the lookup, or a race with another writer — is
    re-fetched fresh on the next attempt instead of repeating the same
    failing payload.
    """
    encoded = base64.b64encode(content.encode()).decode()
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            file_sha = get_file_sha(repo_path, branch)
            payload = {"message": message, "content": encoded, "branch": branch}
            if file_sha:
                payload["sha"] = file_sha
            gh("PUT", f"/contents/{repo_path}", json=payload)
            return
        except RuntimeError as e:
            last_error = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(
        f"push_file: persistent failure for '{repo_path}' after "
        f"{retries + 1} attempts: {last_error}"
    )


def get_file_content(repo_path: str, branch: str) -> str:
    data = gh("GET", f"/contents/{repo_path}", params={"ref": branch})
    return base64.b64decode(data["content"]).decode()


def get_latest_run_id(branch: str) -> Optional[int]:
    """Returns the ID of the currently latest CI run on this branch."""
    runs = gh("GET", "/actions/runs", params={"branch": branch, "per_page": 1}).get("workflow_runs", [])
    return runs[0]["id"] if runs else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def wait_for_ci(branch: str, after_run_id: Optional[int] = None,
                not_before: Optional[datetime] = None
                ) -> tuple[str, str, Optional[datetime]]:
    """
    Waits for a completed CI run on the branch.
    not_before: Only accept runs that started after this UTC timestamp.
    after_run_id: Fallback filter by run ID (legacy support).

    Returns (conclusion, ci_logs, completed_at).
    """
    print(f"  ⏳ Waiting for CI on '{branch}'...", end="", flush=True)
    deadline = time.time() + CI_TIMEOUT

    while time.time() < deadline:
        time.sleep(CI_POLL_SEC)
        runs = gh("GET", "/actions/runs", params={
            "branch": branch,
            "per_page": 5,
        }).get("workflow_runs", [])

        if not runs:
            print(".", end="", flush=True)
            continue

        run = runs[0]  # most recent run

        # Timestamp filter: skip runs that started before our push
        if not_before is not None:
            run_created = datetime.fromisoformat(
                run["created_at"].replace("Z", "+00:00")
            )
            if run_created <= not_before:
                print(".", end="", flush=True)
                continue

        # Legacy filter by run ID
        if after_run_id and run["id"] <= after_run_id:
            print(".", end="", flush=True)
            continue

        if run["status"] == "completed":
            print(f" {run['conclusion']}")
            completed_at = None
            if run.get("updated_at"):
                completed_at = datetime.fromisoformat(
                    run["updated_at"].replace("Z", "+00:00")
                )
            return run["conclusion"], fetch_ci_logs(run["id"]), completed_at

        print(".", end="", flush=True)

    print(" TIMEOUT")
    return "timeout", "", None


def gh_raw_text(path: str) -> str:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}{path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = requests.get(url, headers=headers, allow_redirects=True, timeout=30)
    if resp.status_code >= 400:
        raise RuntimeError(f"GitHub API GET {path} → {resp.status_code}")
    return resp.text


def fetch_ci_logs(run_id: int) -> str:
    jobs = gh("GET", f"/actions/runs/{run_id}/jobs").get("jobs", [])
    parts = []
    for job in jobs:
        conclusion = job.get("conclusion", "")
        parts.append(f"=== JOB: {job['name']} [{conclusion}] ===")
        if conclusion != "failure":
            continue
        try:
            raw_log = gh_raw_text(f"/actions/jobs/{job['id']}/logs")
            lines = []
            for line in raw_log.splitlines():
                clean = re.sub(r'^\d{4}-\d{2}-\d{2}T[\d:.]+Z ', '', line)
                # strip absolute runner paths so the LLM sees relative paths only
                clean = re.sub(r'/home/runner/work/[^/]+/[^/]+/', '', clean)
                if clean.strip() and not clean.startswith('##[group]') and not clean.startswith('##[endgroup]'):
                    lines.append(clean)
            parts.extend(lines[-100:])
        except Exception:
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



def localize_failure(ci_log: str) -> tuple[str, list[str]]:
    """
    Extracts the failure type and affected file paths from the CI log.

    Returns:
        tuple[failure_type, list[affected_file_paths]]
        failure_type: "functional" | "syntactic" |
                      "configurational" | "architectural" | "unknown"
    """
    files: set[str] = set()
    failure_type = "unknown"

    # ── Configurational (pip) ─────────────────────────────────────
    # "ModuleNotFoundError" is included here (not just pip-resolution
    # messages): a genuinely missing dependency doesn't fail `pip install`
    # (nothing wrong with requirements.txt syntactically) — it only
    # surfaces later as a runtime import error. Without this, such a
    # failure would fall through to the generic "FAILED" functional
    # trigger and misclassify the missing dependency as a logic error.
    if any(msg in ci_log for msg in [
        "No matching distribution",
        "ResolutionImpossible",
        "Could not find a version",
        "not find a version that satisfies",
        "ModuleNotFoundError",
    ]):
        failure_type = "configurational"
        files.add("requirements.txt")

    # ── Architectural: ImportError / F4xx flake8 code ────────────────────────
    # "circular import" is intentionally NOT in this list: it appears in the
    # CI log header "=== JOB: Architecture Check (circular imports) ===" for
    # EVERY scenario where lint passes, which would misclassify syntactic and
    # functional failures as architectural.
    # Actual circular import errors always produce "ImportError", "cannot import
    # name", or "partially initialized module" — so "circular import" is redundant.
    elif (any(msg in ci_log for msg in [
        "ImportError",
        "cannot import name",
        "partially initialized module",
    ]) or re.search(r'src/[\w/]+\.py:\d+:\d+: F4', ci_log)):
        failure_type = "architectural"
        for m in re.findall(r'from (src[\w.]+) import', ci_log):
            files.add(m.replace(".", "/") + ".py")
        for m in re.findall(r'(src/[\w/]+\.py):\d+:\d+: F4', ci_log):
            files.add(m)

    # ── Syntactic: E/W/C flake8 codes (style violations) ─────────
    elif any(code in ci_log for code in ["E302", "E711", "E501", "W291"]):
        failure_type = "syntactic"
        for m in re.findall(r'(src/[\w/]+\.py):\d+:\d+:', ci_log):
            files.add(m)

    # ── Functional (pytest) ───────────────────────────────────────
    elif any(msg in ci_log for msg in [
        "FAILED", "AssertionError", "assert"
    ]):
        failure_type = "functional"
        # --tb=long shows function bodies: "from src.calculator import add"
        for m in re.findall(r'from (src(?:\.\w+)+) import', ci_log):
            files.add(m.replace(".", "/") + ".py")
        # Fallback naming convention: FAILED tests/test_X.py → src/X.py
        if not files:
            for m in re.findall(r'FAILED tests/test_(\w+)\.py', ci_log):
                files.add(f"src/{m}.py")

    return failure_type, list(files)


def localize_functional_source(
    ci_log: str,
    known_files: list[str],
    known_contents: dict[str, str],
) -> list[str]:
    """
    LLM call for functional-regression file localization. Always invoked
    (in addition to the deterministic regex match in localize_failure()),
    so the LLM can confirm or extend the file set — e.g. propose a shared
    helper module that the regex-only extraction cannot see, since it only
    matches the import statement literally present in the failing test's
    own source code, not the internal call graph of the file it imports.
    """
    context_block = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in known_contents.items()
    ) or "(none identified yet)"

    prompt = f"""A CI pipeline failed with the following pytest output:
---
{ci_log[:1500]}
---

Source file(s) already identified as directly involved:
{context_block}

Based on the failing test and the file(s) shown above (if any), list ALL
source files under src/ that need to be fixed to resolve this failure.
Include the file(s) already shown above if they are still relevant, and add
any additional file (e.g. a shared helper module imported by the code
above) that also needs to change to make the test pass.

Respond with ONLY a JSON array of file paths, no other text:
["src/example.py"]"""

    raw = ollama_generate(prompt)
    try:
        clean = (raw.strip()
                 .removeprefix("```json")
                 .removeprefix("```")
                 .removesuffix("```")
                 .strip())
        result = json.loads(clean)
        return result if isinstance(result, list) and result else known_files
    except Exception:
        return known_files


# ── Prompt builder ────────────────────────────────────────────────────────────

_TYPE_HINTS: dict[str, str] = {
    "functional": (
        "The tests are correct — the source code has a logic error. "
        "The CI log above shows the failing assertion(s): the actual/left side is what "
        "the current (buggy) code produces, the expected/right side is what it should "
        "produce. Use that diff, not assumptions, to find the bug — compare actual vs. "
        "expected for every failing case to infer exactly which operator, condition, or "
        "computation is wrong (this can be anything: operator choice, comparison "
        "direction, argument order, nesting — the log tells you which, don't guess). "
        "Fix the implementation so it produces the expected value for every case shown "
        "in the log, not just the first one."
    ),
    "configurational": (
        "The CI log above names the exact dependency problem — look for 'Could not "
        "find a version', 'No matching distribution', 'ResolutionImpossible', or "
        "'ModuleNotFoundError'. "
        "If pip's error lists available versions (often after 'from versions:'), the "
        "fix MUST use one of those exact listed versions — do not invent or guess a "
        "version number that does not appear in the log. "
        "If the error is a ModuleNotFoundError instead (the package is missing from "
        "requirements.txt entirely, not just pinned wrong), add an entry for it using a "
        "well-known, stable release; note the import name in the error (e.g. 'yaml') is "
        "not always the same as the PyPI package name (e.g. 'PyYAML'). "
        "Only change or add the dependency the CI log actually names — leave every "
        "other line in requirements.txt untouched."
    ),
    "architectural": (
        "The CI log above names the exact cause of the circular dependency — look for "
        "'ImportError', 'cannot import name', 'partially initialized module', or an "
        "'imported but unused' (F401) warning; that message identifies which specific "
        "module each broken file must stop importing from. DELETE only the import "
        "line(s) that import from that module. "
        "Every OTHER import in the file is unrelated to the circular dependency: before "
        "deleting or changing any import line, check whether the names it imports are "
        "still referenced anywhere in the file's code. If they are, that import line "
        "MUST be kept exactly as-is. "
        "CRITICAL: leaving in the import line named by the CI error reproduces the same "
        "failure and fails CI. "
        "CRITICAL: deleting a still-used import that the CI log did NOT flag causes "
        "NameError and also fails CI. "
        "Do NOT invent or reference files that are not listed under FILES TO FIX."
    ),
}


def build_prompt(failure_type: str, ci_logs: str,
                 broken_files: dict[str, str],
                 context_files: dict[str, str] = None) -> str:

    files_block = "\n\n".join(
        f"### FILE TO FIX: {path}\n```\n{content}\n```"
        for path, content in broken_files.items()
    )

    context_block = ""
    if context_files:
        context_block = "\n\nCONTEXT FILES (read-only reference, do NOT patch):\n"
        context_block += "\n\n".join(
            f"### CONTEXT: {path}\n```\n{content}\n```"
            for path, content in context_files.items()
        )

    hint = _TYPE_HINTS.get(failure_type, "")
    hint_block = f"\n\nADDITIONAL CONTEXT:\n{hint}" if hint else ""

    file_list = "\n".join(f"  {p}" for p in broken_files)

    return f"""You are an expert DevOps engineer fixing a CI/CD pipeline failure.

FAILURE TYPE: {failure_type}

CI LOG:
---
{ci_logs[:2000]}
---

{files_block}{context_block}{hint_block}

INSTRUCTIONS:
- Fix the failure shown in the CI log.
- Return the COMPLETE corrected content for each broken file.
- Only fix FILES TO FIX. Never modify CONTEXT FILES.
- Only reference symbols that exist in the codebase.

Respond in EXACTLY this format (no other text):

{{"analysis": "<one sentence root-cause>"}}
===FILE: <exact path>===
<complete corrected file content>
===ENDFILE===

Files to fix:
{file_list}"""


def parse_patch(raw: str) -> Optional[dict]:
    # Extract JSON metadata from any line that looks like a self-contained JSON object
    metadata: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                metadata = json.loads(line)
                break
            except json.JSONDecodeError:
                pass

    # Extract ===FILE: path===...===ENDFILE=== blocks (no JSON escaping needed)
    patches = []
    for m in re.finditer(r'===FILE:[ \t]*([\w./ -]+?)[ \t]*===\n?([\s\S]*?)===ENDFILE===', raw):
        filename = m.group(1).strip().lstrip('/')
        content  = m.group(2).strip("\n")
        patches.append({"filename": filename, "fixed_content": content})

    if patches:
        return {
            "analysis": metadata.get("analysis", ""),
            "patches":  patches,
        }

    # Fallback: standard JSON parse (catches cases where the model ignores format instructions)
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


def strip_code_fences(content: str) -> str:
    """Removes leading/trailing Markdown code fences from the file content."""
    lines = content.strip().splitlines()
    if lines and re.match(r'^```[\w]*$', lines[0].strip()):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    return '\n'.join(lines)


def fix_blank_lines(content: str) -> str:
    """Ensures 2 blank lines precede top-level def/class (PEP8 E302)."""
    lines = content.split('\n')
    result = []
    for i, line in enumerate(lines):
        if line.startswith(('def ', 'class ')) and result:
            blanks = 0
            j = len(result) - 1
            while j >= 0 and result[j].strip() == '':
                blanks += 1
                j -= 1
            while blanks < 2:
                result.append('')
                blanks += 1
        result.append(line)
    return '\n'.join(result)


# ── Healing loop ──────────────────────────────────────────────────────────────

def heal_scenario(scenario: Scenario, run_index: int) -> list[RunResult]:
    results = []
    branch  = f"experiment/{scenario.id}-run{run_index}"

    print(f"\n{'─'*60}")
    print(f"Scenario : {scenario.id}  run={run_index}")
    print(f"Branch   : {branch}")

    # Record latest run ID *before* deleting, so we can filter out old runs
    # from prior experiments that GitHub keeps associated with this branch name.
    pre_delete_id = get_latest_run_id(branch) or 0

    delete_branch(branch)   # remove stale branch from any previous run
    create_branch(branch)

    # Wait for the branch-creation CI run (healthy code → would pass) to be
    # registered in GitHub Actions. We record its ID as a baseline so that
    # wait_for_ci() can use after_run_id to guarantee it only accepts the run
    # triggered by our broken-file injection — not the healthy branch-creation run.
    # Without this, a race exists: GitHub schedules the clean run asynchronously
    # and its created_at may land after t_inject, bypassing the not_before filter.
    baseline_run_id = pre_delete_id
    for _ in range(12):  # poll up to 60 s (12 × 5 s)
        time.sleep(5)
        rid = get_latest_run_id(branch)
        if rid and rid > pre_delete_id:
            baseline_run_id = rid
            print(f"  ✓ Branch-creation CI run registered (id={baseline_run_id})")
            break

    # Inject broken files
    t_inject = utc_now()
    for repo_path, local_path in scenario.broken_files.items():
        with open(local_path) as f:
            broken_content = f.read()
        push_file(repo_path, broken_content,
                  f"[experiment] inject regression: {scenario.id}", branch)
        print(f"  ✓ Injected: {repo_path}")

    # Wait for CI to fail — only accept runs with ID > baseline_run_id (= triggered by injection)
    conclusion, ci_logs, failure_confirmed_at = wait_for_ci(
        branch, not_before=t_inject, after_run_id=baseline_run_id
    )
    if conclusion == "success":
        print("  ⚠  CI passed with broken files — check scenario definition")
        return results

    print(f"  ✗ CI failed as expected")

    # MTTR clock starts here, per DORA's Mean Time To Restore definition:
    # from confirmed failure detection to confirmed recovery. Branch setup,
    # baseline-run polling, and fault injection above are experimental
    # scaffolding needed to *produce* the incident, not part of recovering
    # from it, so they are excluded from the measured duration. Uses
    # GitHub's own completion timestamp for the failing run (consistent
    # with how the recovery endpoint is measured below) rather than the
    # local time.time() at which our poll happened to observe it.
    t_failure_detected = (
        failure_confirmed_at.timestamp() if failure_confirmed_at else time.time()
    )

    # Dynamic fault localization from the CI log
    failure_type, affected_files = localize_failure(ci_logs)
    detected_type = failure_type
    gt = scenario.ground_truth_type
    match_sym = ("✓" if detected_type == gt else "✗") if gt else ""
    gt_label   = f" (ground truth: '{gt}')" if gt else ""
    print(f"  → Failure type detected: {detected_type} {match_sym}{gt_label}")
    print(f"  → Affected files: {affected_files}")

    # Functional special case: first extend the candidate set deterministically,
    # then confirm/extend it via LLM. The regex in localize_failure() only sees
    # the import statement literally present in the test code itself, not the
    # internal call graph of the file it imports — a bug in an imported helper
    # file (e.g. utils.py, imported by calculator.py) would otherwise be invisible.
    if failure_type == "functional":
        candidates = []
        for tf in affected_files:
            candidate = tf.replace("tests/test_", "src/")
            if get_file_sha(candidate, branch):
                candidates.append(candidate)

        known_contents = {}
        for path in candidates:
            try:
                known_contents[path] = get_file_content(path, branch)
            except RuntimeError:
                pass

        # Deterministic extension: any src/ module imported by an already-known
        # file is automatically added as a candidate — does not rely on LLM inference.
        for m in re.findall(r'from (src(?:\.\w+)+) import',
                             "\n".join(known_contents.values())):
            extra_path = m.replace(".", "/") + ".py"
            if extra_path not in candidates and get_file_sha(extra_path, branch):
                try:
                    known_contents[extra_path] = get_file_content(extra_path, branch)
                    candidates.append(extra_path)
                except RuntimeError:
                    pass

        print("  Calling LLM for functional source localization...",
              end="", flush=True)
        llm_files = localize_functional_source(ci_logs, candidates, known_contents)
        print(f" {llm_files}")
        # Union instead of replacement: the LLM call can only add candidates,
        # never silently discard one that was found deterministically.
        affected_files = list(dict.fromkeys(candidates + llm_files))

    if not affected_files:
        print("  ⚠  No affected files identified — skipping")
        return results

    # Load file contents from the branch
    broken_contents: dict[str, str] = {}
    for repo_path in affected_files:
        try:
            broken_contents[repo_path] = get_file_content(repo_path, branch)
        except RuntimeError:
            print(f"  ⚠  Could not read {repo_path}")

    if not broken_contents:
        print("  ⚠  Could not read any files to fix — aborting scenario")
        return results

    context_contents: dict[str, str] = {}
    for repo_path in scenario.context_files:
        try:
            context_contents[repo_path] = get_file_content(repo_path, branch)
        except RuntimeError:
            print(f"  ⚠  Could not read context file {repo_path}")

    # Attempt fixes
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n  [Attempt {attempt}/{MAX_ATTEMPTS}]")
        result = RunResult(
            scenario_id=scenario.id, attempt=attempt, branch=branch,
            detected_type=detected_type,
        )

        # LLM call
        prompt = build_prompt(
            detected_type, ci_logs, broken_contents, context_contents
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

        print(f"  → {patch_data.get('analysis', 'N/A')}")

        # Apply patches — full file replacement
        known        = set(affected_files)
        diff_lines   = []
        apply_ok     = True
        t_last_push  = utc_now()  # updated just before each push; final value = last push time
        patches_list = patch_data.get("patches", [])
        for patch in patches_list:
            repo_path     = patch.get("filename")
            fixed_content = patch.get("fixed_content", "")

            if repo_path not in known:
                # Normalize runner absolute paths by matching known file suffixes
                # e.g. "home/runner/work/repo/repo/requirements.txt" → "requirements.txt"
                normalized = next(
                    (k for k in known if repo_path == k or repo_path.endswith('/' + k)),
                    None,
                )
                if normalized:
                    print(f"  ~ Normalized path '{repo_path}' → '{normalized}'")
                    repo_path = normalized
                else:
                    result.error = f"Unknown file path: '{repo_path}'"
                    print(f"  ⚠  {result.error}")
                    apply_ok = False
                    break

            if not fixed_content.strip():
                result.error = f"Empty fixed_content for {repo_path}"
                print(f"  ✗ {result.error}")
                apply_ok = False
                break

            fixed_content = strip_code_fences(fixed_content)
            fixed_content = fix_blank_lines(fixed_content)
            if not fixed_content.endswith('\n'):
                fixed_content += '\n'

            # Timestamp just before the push so wait_for_ci skips CI runs from
            # earlier pushes (intermediate states) in multi-file scenarios.
            t_last_push = utc_now()
            push_file(
                repo_path, fixed_content,
                f"[agent] fix attempt {attempt}: {patch_data.get('analysis','')[:60]}",
                branch,
            )
            broken_contents[repo_path] = fixed_content
            diff_lines.append(f"Replaced {repo_path} (full file)")
            print(f"  ✓ Replaced: {repo_path}")

        if not apply_ok:
            results.append(result)
            continue

        result.patch_applied = True
        result.patch_diff    = "\n".join(diff_lines)

        # Only accept the CI run triggered by the LAST push (not intermediate runs)
        conclusion, ci_logs, completed_at = wait_for_ci(branch, not_before=t_last_push)
        result.ci_green = conclusion == "success"

        if result.ci_green:
            # Prefer GitHub's own completion timestamp over the local
            # time.time() at which our poll happened to observe it —
            # removes up to CI_POLL_SEC seconds of polling jitter and
            # local API round-trip latency from the TTR measurement.
            recovery_end = completed_at.timestamp() if completed_at else time.time()
            result.time_to_recovery = recovery_end - t_failure_detected
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
            description="Wrong arithmetic operators in calculator.py",
            broken_files={"src/calculator.py": f"{base}/functional_regression.py"},
            ground_truth_type="functional",
        ),
        Scenario(
            id="functional_regression_2",
            description="Inverted clamp() logic in utils.py",
            broken_files={"src/utils.py": f"{base}/functional_regression_2.py"},
            ground_truth_type="functional",
        ),
        Scenario(
            id="functional_regression_3",
            description=(
                "average() in calculator.py fails because safe_round() in "
                "utils.py rounds off by one digit — the buggy file is not the "
                "one directly imported by the failing test."
            ),
            broken_files={"src/utils.py": f"{base}/functional_regression_3.py"},
            ground_truth_type="functional",
        ),
        Scenario(
            id="syntactic_regression",
            description="PEP8 violations in utils.py",
            broken_files={"src/utils.py": f"{base}/syntactic_regression.py"},
            ground_truth_type="syntactic",
        ),
        Scenario(
            id="syntactic_regression_2",
            description="E711 comparison to None in data.py",
            broken_files={"src/data.py": f"{base}/syntactic_regression_2.py"},
            ground_truth_type="syntactic",
        ),
        Scenario(
            id="syntactic_regression_3",
            description=(
                "Three simultaneous, independent flake8 violations in data.py "
                "(E711 + F841 + E501) — tests whether the agent fixes all of "
                "them in one pass without missing one or over-fixing."
            ),
            broken_files={"src/data.py": f"{base}/syntactic_regression_3.py"},
            ground_truth_type="syntactic",
        ),
        Scenario(
            id="configurational_regression",
            description="Nonexistent package version in requirements.txt",
            broken_files={"requirements.txt": f"{base}/configurational_regression.txt"},
            ground_truth_type="configurational",
        ),
        Scenario(
            id="configurational_regression_2",
            description="Nonexistent pytest version in requirements.txt",
            broken_files={"requirements.txt": f"{base}/configurational_regression_2.txt"},
            ground_truth_type="configurational",
        ),
        Scenario(
            id="configurational_regression_3",
            description=(
                "PyYAML is missing entirely from requirements.txt (not a "
                "wrong version pin) — pip install succeeds, the failure "
                "only appears as a ModuleNotFoundError at test time."
            ),
            broken_files={"requirements.txt": f"{base}/configurational_regression_3.txt"},
            ground_truth_type="configurational",
        ),
        Scenario(
            id="architectural_regression",
            description="Circular import between user.py and order.py",
            broken_files={
                "src/services/user.py":  f"{base}/architectural_regression_user.py",
                "src/services/order.py": f"{base}/architectural_regression_order.py",
            },
            ground_truth_type="architectural",
        ),
        Scenario(
            id="architectural_regression_2",
            description=(
                "Circular import that is functionally load-bearing (cross-service "
                "order/user lookups) — deleting the import breaks behaviour; the "
                "correct fix routes through the shared src.data layer instead."
            ),
            broken_files={
                "src/services/user.py":  f"{base}/architectural_regression_2_user.py",
                "src/services/order.py": f"{base}/architectural_regression_2_order.py",
            },
            context_files=["src/data.py"],
            ground_truth_type="architectural",
        ),
        Scenario(
            id="architectural_regression_3",
            description=(
                "Three-file circular import chain (user -> payment -> order -> "
                "user), every edge functionally load-bearing — extends "
                "architectural_regression_2 from a two-file to a three-file "
                "cycle; the correct fix routes all three edges through the "
                "shared src.data layer instead."
            ),
            broken_files={
                "src/services/user.py":    f"{base}/architectural_regression_3_user.py",
                "src/services/order.py":   f"{base}/architectural_regression_3_order.py",
                "src/services/payment.py": f"{base}/architectural_regression_3_payment.py",
            },
            context_files=["src/data.py"],
            ground_truth_type="architectural",
        ),
    ]


# ── Experiment runner ─────────────────────────────────────────────────────────

def run_experiment(runs_per_scenario: int = 10):
    if not GITHUB_TOKEN:
        raise SystemExit("ERROR: Set GITHUB_TOKEN environment variable first.")
    if not ollama_available():
        raise SystemExit("ERROR: Ollama not running. Start with: ollama serve")

    scenarios    = get_scenarios()
    all_results: list[RunResult] = []

    print(f"\n{'═'*60}")
    print(f"  THESIS EXPERIMENT — v7 (fully autonomous)")
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
    print(f"{'Scenario':<35} {'FSR':>5} {'VPR':>5} {'MTTR':>8}")
    print(f"{'─'*60}")

    for scenario in scenarios:
        s_res = [r for r in all_results if r.scenario_id == scenario.id]
        total = len(s_res)
        if not total:
            continue
        fsr = sum(1 for r in s_res if r.ci_green) / runs_per_scenario
        vpr = sum(1 for r in s_res if r.patch_applied) / total
        ttrs = [r.time_to_recovery for r in s_res if r.time_to_recovery]
        mttr = f"{sum(ttrs)/len(ttrs):.0f}s" if ttrs else "N/A"
        print(f"{scenario.id:<35} {fsr:>4.0%}  {vpr:>4.0%}  {mttr:>7}")

    print(f"{'─'*60}")
    print("FSR | VPR | MTTR")

    # Classification accuracy across all runs
    type_map = {s.id: s.ground_truth_type for s in scenarios}
    classified = [r for r in all_results if r.detected_type]
    if classified:
        correct = sum(1 for r in classified if r.detected_type == type_map.get(r.scenario_id))
        print(f"\nClassification accuracy: {correct}/{len(classified)} "
              f"({correct/len(classified):.0%})")

    ts  = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.makedirs("results", exist_ok=True)
    out = f"results/experiment_{ts}.json"
    with open(out, "w") as f:
        json.dump([asdict(r) for r in all_results], f, indent=2, default=str)
    print(f"\nFull results → {out}")


if __name__ == "__main__":
    run_experiment(runs_per_scenario=30)

