"""Repeatable evaluation runner.

    python -m evals.run                    # mock provider (offline, deterministic)
    python -m evals.run --provider anthropic

Runs every golden case through the FULL pipeline on an isolated temp DB and scores:
routing accuracy, category accuracy, compliance recall (expected reason codes found),
attribute accuracy (pack/sizes/colors), title hygiene, and injection short-circuit.
Writes evals/results/eval_<ts>.json, latest.json and latest.md."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock")
    args = ap.parse_args()
    os.environ["LLM_PROVIDER"] = args.provider
    tmp = tempfile.mkdtemp(prefix="evaldb_")
    os.environ["CATALOG_DB_PATH"] = str(Path(tmp) / "eval.db")

    from app.config import path
    from app.data.seed_synthetic import seed
    from app.services import workflow

    seed(reset=True)
    cases = [json.loads(l) for l in (ROOT / "golden.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, counters = [], {k: [0, 0] for k in ("routing", "category", "compliance_recall", "attributes", "title", "injection_short_circuit")}

    def score(metric, ok):
        counters[metric][1] += 1
        counters[metric][0] += int(ok)
        return ok

    t0 = time.perf_counter()
    for c in cases:
        exp, inp = c["expected"], c["input"]
        checks: dict[str, bool] = {}
        try:
            r = workflow.submit_listing(supplier_id="EVAL", supplier_sku=c["id"], actor="eval", **inp)
            codes = {v.code for v in r.violations}
            checks["routing"] = score("routing", r.status == exp["status"])
            if "category_id" in exp:
                checks["category"] = score("category", r.category_id == exp["category_id"])
            for code in exp.get("reason_codes", []) + exp.get("warn_codes", []):
                checks[f"code:{code}"] = score("compliance_recall", code in codes)
            for k, attr in (("pack_count", None), ("sizes", "size"), ("colors", "colors")):
                if k in exp:
                    got = r.extracted.pack_count if (k == "pack_count" and r.extracted) else r.attributes.get(attr)
                    checks[k] = score("attributes", got == exp[k])
            for s in exp.get("title_contains", []):
                checks[f"title+{s}"] = score("title", s in (r.clean_title or ""))
            for s in exp.get("title_excludes", []):
                checks[f"title-{s}"] = score("title", s not in (r.clean_title or ""))
            if "llm_calls" in exp:
                checks["llm_calls"] = score("injection_short_circuit",
                                            r.classification_attempts + r.extraction_attempts == exp["llm_calls"])
            detail = {"status": r.status, "category_id": r.category_id, "confidence": r.category_confidence,
                      "clean_title": r.clean_title, "codes": sorted(codes), "attributes": r.attributes}
        except Exception as e:  # a crash is a failure, never silently skipped
            checks["no_exception"] = False
            detail = {"error": f"{type(e).__name__}: {e}"}
        passed = all(checks.values())
        rows.append({"id": c["id"], "type": c["type"], "desc": c["desc"], "passed": passed,
                     "failed_checks": [k for k, v in checks.items() if not v], "result": detail})

    total, n_pass = len(rows), sum(r["passed"] for r in rows)
    metrics = {k: {"passed": a, "total": b, "rate": round(a / b, 4) if b else None} for k, (a, b) in counters.items()}
    by_type = {}
    for t in ("positive", "negative", "edge"):
        sub = [r for r in rows if r["type"] == t]
        by_type[t] = {"passed": sum(r["passed"] for r in sub), "total": len(sub)}
    status = workflow.llm_status()
    summary = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": status["provider"], "model": status["model"],
               "total": total, "passed": n_pass, "pass_rate": round(n_pass / total, 4), "by_type": by_type,
               "metrics": metrics, "duration_s": round(time.perf_counter() - t0, 2),
               "failures": [r for r in rows if not r["passed"]], "cases": rows,
               "data_label": "SYNTHETIC golden set"}
    out = path("eval_results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"eval_{stamp}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out / "latest.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    md = [f"# Eval results ({summary['ts']}, provider={summary['provider']}, model={summary['model']})", "",
          f"**Overall: {n_pass}/{total} cases passed ({summary['pass_rate']:.0%})**", "",
          "| Metric | Passed | Total | Rate |", "|---|---|---|---|"]
    def pct(x):
        return "n/a" if x is None else f"{x:.0%}"
    md += [f"| {k} | {v['passed']} | {v['total']} | {pct(v['rate'])} |" for k, v in metrics.items()]
    md += ["", "| Case type | Passed | Total |", "|---|---|---|"] + [f"| {k} | {v['passed']} | {v['total']} |" for k, v in by_type.items()]
    md += ["", "## Failures"] + ([f"- **{f['id']}** ({f['desc']}): failed {f['failed_checks']} -> {f['result']}"
                                  for f in summary["failures"]] or ["- none"])
    (out / "latest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0 if n_pass == total else 1


if __name__ == "__main__":
    sys.exit(main())
