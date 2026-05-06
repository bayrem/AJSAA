"""Scoring baseline comparison across llm / hybrid / static modes.

Runs each mode twice against 6 representative jobs from the last dry run,
then prints a side-by-side table showing score variation across methods and rounds.

Usage:
    .venv/bin/python scripts/scoring_baseline.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Make sure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("PYTHONPATH", str(Path(__file__).parent.parent))

# ── Job selection ──────────────────────────────────────────────────────────────
SELECTED_IDS = [
    "62980c112573a05e",  # [92] Senior PM - Data Platform @ Doctolib
    "5e69c62052dd4dec",  # [84] PM Forge @ Mistral AI
    "de0683569628bdd1",  # [81] AI PM - IA Reference @ Collective.work
    "ec526d614033ab00",  # [76] PM IA @ LegalPlace
    "1f99bc5f6c21cc37",  # [72] PM - Data & Digital Transformation @ Wivoo — fallback below
    "b8a5e2e3c7d4f9a1",  # [71] ML PM @ Akur8 — fallback below
]

JOBS_PATH = Path(".data/jobs.json")
CV_PATH   = Path("query/resume/cv1_ai_enablement.md")


def load_jobs() -> list[dict]:
    all_jobs = json.loads(JOBS_PATH.read_text())
    by_id = {j["job_id"]: j for j in all_jobs}
    jobs = [by_id[jid] for jid in SELECTED_IDS if jid in by_id]
    # Fall back to first N jobs if IDs changed
    if len(jobs) < 6:
        seen = {j["job_id"] for j in jobs}
        for j in all_jobs:
            if j["job_id"] not in seen:
                jobs.append(j)
                seen.add(j["job_id"])
            if len(jobs) == 6:
                break
    return jobs[:6]


def load_cv() -> dict:
    content = CV_PATH.read_text(encoding="utf-8")
    return {"name": "cv1_ai_enablement", "content": content}


def compress_cv(llm, cv: dict) -> dict:
    from providers.scoring.cv_cache import get_or_compress
    compressed = get_or_compress(llm, cv)
    return {"name": cv["name"], "content": compressed}


def run_llm(llm, jobs, compressed_cvs, scoring_cfg) -> dict:
    from agent.nodes.analyze_jobs import score_jobs_batch
    results = score_jobs_batch(llm, jobs, compressed_cvs, scoring_cfg)
    return {j["job_id"]: j["score"] for j in results}


def run_static(jobs, profiles_dir, scoring_cfg) -> dict:
    from providers.scoring.profile_store import content_hash, load_profile
    from providers.scoring.static_scorer import score_jobs_static

    cv = load_cv()
    cv_hash = content_hash(cv["content"])
    profile = load_profile("cv1_ai_enablement", cv_hash, profiles_dir)
    if profile is None:
        return {j["job_id"]: "NO_PROFILE" for j in jobs}

    results = score_jobs_static(jobs, {"cv1_ai_enablement": profile}, scoring_cfg)
    scored = {j["job_id"]: j["score"] for j in results}
    # Jobs that didn't pass threshold
    for j in jobs:
        if j["job_id"] not in scored:
            scored[j["job_id"]] = f"<{scoring_cfg.get('min_score', 70)}"
    return scored


def run_hybrid(llm, jobs, cv, compressed_cvs, scoring_cfg, profiles_dir) -> dict:
    from providers.scoring.hybrid_scorer import HybridScorer
    cfg = {**scoring_cfg, "profiles_dir": profiles_dir}
    scorer = HybridScorer(llm, [cv], compressed_cvs, cfg)
    results = scorer.score(jobs)
    scored = {j["job_id"]: j["score"] for j in results}
    for j in jobs:
        if j["job_id"] not in scored:
            scored[j["job_id"]] = f"<{scoring_cfg.get('min_score', 70)}"
    return scored


def fmt(score) -> str:
    return f"{score:>3}" if isinstance(score, int) else str(score)


def print_table(jobs, results: dict):
    labels = {
        "llm_1":    "LLM r1",
        "llm_2":    "LLM r2",
        "hybrid_1": "Hyb r1",
        "hybrid_2": "Hyb r2",
        "static_1": "Sta r1",
        "static_2": "Sta r2",
    }
    cols = list(labels.keys())
    header = f"{'Job':<46} {'Ref':>4}  " + "  ".join(f"{labels[c]:>6}" for c in cols)
    print("\n" + "─" * len(header))
    print(header)
    print("─" * len(header))
    for j in jobs:
        jid   = j["job_id"]
        ref   = j.get("score", "?")
        label = f"{j.get('title','?')[:32]} @ {j.get('company','?')[:12]}"
        scores = "  ".join(f"{fmt(results[c].get(jid, '—')):>6}" for c in cols)
        print(f"{label:<46} {ref:>4}  {scores}")
    print("─" * len(header))


def main():
    import yaml
    from dotenv import load_dotenv
    load_dotenv()

    cfg_raw = yaml.safe_load(Path("config.yaml").read_text())
    llm_cfg = cfg_raw["llm"]
    scoring_cfg = {
        **cfg_raw.get("scoring", {}),
        "min_score": 70,
        "max_score": 95,
        "uncertainty_band": [60, 80],
    }

    from providers.llm.factory import build_llm
    llm = build_llm(llm_cfg)

    jobs = load_jobs()
    cv   = load_cv()

    print(f"\nLoaded {len(jobs)} jobs. Compressing CV (cache or LLM)…")
    compressed_cvs = [compress_cv(llm, cv)]
    print("CV ready.\n")

    # Isolated profiles dir so test doesn't touch real scoring_profiles/
    profiles_dir = tempfile.mkdtemp(prefix="ajsaa_baseline_")
    print(f"Profiles dir: {profiles_dir}\n")

    results: dict[str, dict] = {}

    try:
        print("── LLM round 1 ─────────────────────────────────────────")
        t0 = time.time()
        results["llm_1"] = run_llm(llm, jobs, compressed_cvs, scoring_cfg)
        print(f"   done in {time.time()-t0:.1f}s\n")

        print("── LLM round 2 ─────────────────────────────────────────")
        t0 = time.time()
        results["llm_2"] = run_llm(llm, jobs, compressed_cvs, scoring_cfg)
        print(f"   done in {time.time()-t0:.1f}s\n")

        print("── Hybrid round 1 (bootstrap → saves profile) ──────────")
        t0 = time.time()
        results["hybrid_1"] = run_hybrid(llm, jobs, cv, compressed_cvs, scoring_cfg, profiles_dir)
        print(f"   done in {time.time()-t0:.1f}s\n")

        print("── Hybrid round 2 (static from saved profile) ──────────")
        t0 = time.time()
        results["hybrid_2"] = run_hybrid(llm, jobs, cv, compressed_cvs, scoring_cfg, profiles_dir)
        print(f"   done in {time.time()-t0:.1f}s\n")

        print("── Static round 1 (deterministic) ───────────────────────")
        t0 = time.time()
        results["static_1"] = run_static(jobs, profiles_dir, scoring_cfg)
        print(f"   done in {time.time()-t0:.1f}s\n")

        print("── Static round 2 (deterministic) ───────────────────────")
        t0 = time.time()
        results["static_2"] = run_static(jobs, profiles_dir, scoring_cfg)
        print(f"   done in {time.time()-t0:.1f}s\n")

    finally:
        shutil.rmtree(profiles_dir, ignore_errors=True)

    print_table(jobs, results)

    # Variation summary
    print("\nVariation summary:")
    print(f"  {'Job':<46}  LLM δ  Hyb δ  Sta δ  LLM↔Hyb2  LLM↔Sta")
    print("  " + "─" * 80)
    for j in jobs:
        jid = j["job_id"]
        label = f"{j.get('title','?')[:32]} @ {j.get('company','?')[:12]}"

        def _delta(k1, k2):
            v1, v2 = results[k1].get(jid), results[k2].get(jid)
            if isinstance(v1, int) and isinstance(v2, int):
                return abs(v1 - v2)
            return "—"

        def _diff(k1, k2):
            v1, v2 = results[k1].get(jid), results[k2].get(jid)
            if isinstance(v1, int) and isinstance(v2, int):
                return v2 - v1
            return "—"

        llm_d  = _delta("llm_1", "llm_2")
        hyb_d  = _delta("hybrid_1", "hybrid_2")
        sta_d  = _delta("static_1", "static_2")
        lh2    = _diff("llm_1", "hybrid_2")
        ls     = _diff("llm_1", "static_1")

        def fmt_v(v):
            return f"{v:>+4}" if isinstance(v, int) else f"{'—':>4}"

        print(f"  {label:<46}  {fmt_v(llm_d):>5}  {fmt_v(hyb_d):>5}  {fmt_v(sta_d):>5}  "
              f"{fmt_v(lh2):>8}  {fmt_v(ls):>6}")

    print()


if __name__ == "__main__":
    main()
