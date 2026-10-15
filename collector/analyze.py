"""Summarize what the collected postings ask for.

Groups postings into role families, then counts how often each tool, method,
and requirement is mentioned, overall and per family. Writes data/analysis.json
and a readable data/analysis.md.

Run from the repo root after a collection:  python -m collector.analyze
"""
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# First match wins, so order runs from most to least specific.
FAMILIES = [
    ("Assessment & measurement", r"psychometric|measurement|assessment|item (writer|analyst|development)|test development|scoring"),
    ("Institutional research", r"institutional (research|effectiveness)|enrollment|student success|retention analyst|registrar"),
    ("Program evaluation", r"evaluat|impact|efficacy"),
    ("Learning science & engineering", r"learning (scien|engineer)|learning analytics"),
    ("Policy & social research", r"research (analyst|associate|assistant|scientist|fellow)|researcher|policy|city research scientist"),
    ("Product & business analytics", r"product analyst|business analyst|insights|growth|marketing"),
    ("Data engineering & analytics engineering", r"data engineer|analytics engineer|database|etl|data architect"),
    ("Data science", r"data scien|machine learning|statistician|quantitative"),
    ("General data analysis", r"data|analyst|analytics|reporting|metrics"),
]
FAMILY_RES = [(name, re.compile(pat, re.I)) for name, pat in FAMILIES]

TERMS = {
    # languages and tools
    "SQL": r"\bsql\b",
    "Python": r"\bpython\b",
    "R": r"(?<![\w-])R(?![\w-])(?=[\s,/;)]|$)",
    "Stata": r"\bstata\b",
    "SAS": r"\bsas\b",
    "SPSS": r"\bspss\b",
    "Excel": r"\bexcel\b",
    "Tableau": r"\btableau\b",
    "Power BI": r"\bpower ?bi\b",
    "Looker": r"\blooker\b",
    "dbt": r"\bdbt\b",
    "Snowflake": r"\bsnowflake\b",
    "BigQuery / GCP": r"\bbigquery\b|google cloud",
    "AWS": r"\baws\b|amazon web services",
    "Git/GitHub": r"\bgit(hub)?\b",
    "GIS": r"\b(arc)?gis\b|geospatial",
    # methods
    "Regression": r"regression",
    "Causal inference / quasi-experimental": r"causal|quasi-experiment|difference-in-difference|regression discontinuity|propensity",
    "Experiments / RCTs": r"randomi[sz]ed|\brct\b|a/b test|experimental design",
    "Survey methods": r"\bsurvey",
    "Longitudinal / panel data": r"longitudinal|panel data",
    "Psychometrics (IRT, validity, reliability)": r"psychometric|item response|\birt\b|reliability|validity",
    "Machine learning": r"machine learning|predictive model",
    "Qualitative methods": r"qualitative|interview|focus group",
    "Data visualization / dashboards": r"visuali[sz]|dashboard",
    "Administrative data": r"administrative data|admin data",
    # requirements and context
    "Writing / reports": r"writ(ing|ten)|report",
    "Explaining to non-technical audiences": r"non-?technical|lay audience|plain language",
    "K-12 or school experience": r"k-?12|school district|classroom|teach(ing|er) experience",
    "Master's preferred or required": r"master'?s",
    "AI / LLM tools": r"\b(ai|genai|generative ai|llm|large language model|chatgpt|prompt)\b",
}
TERM_RES = {k: re.compile(v, re.I) for k, v in TERMS.items()}


def family_of(job):
    title = job.get("title") or ""
    for name, rx in FAMILY_RES:
        if rx.search(title):
            return name
    return "Other"


def mentions(job):
    text = " ".join([job.get("title") or "", job.get("description") or ""])
    return {k for k, rx in TERM_RES.items() if rx.search(text)}


def _share(counter, n):
    return {k: round(100 * v / n) for k, v in counter.most_common()} if n else {}


def summarize(jobs):
    jobs = [j for j in jobs if (j.get("description") or "").strip()]
    by_family = defaultdict(list)
    for job in jobs:
        by_family[family_of(job)].append(job)

    def block(group):
        terms = Counter()
        years, pay = [], []
        for job in group:
            terms.update(mentions(job))
            if job.get("years_required"):
                years.append(job["years_required"])
            if job.get("salary_max"):
                pay.append(job["salary_max"])
        return {
            "postings": len(group),
            "term_share_pct": _share(terms, len(group)),
            "years_required": {
                "postings_stating": len(years),
                "median": statistics.median(years) if years else None,
                "share_0_to_2": round(100 * sum(1 for y in years if y <= 2) / len(years)) if years else None,
            },
            "salary_max": {
                "postings_stating": len(pay),
                "median": statistics.median(pay) if pay else None,
            },
            "phd_required_pct": round(100 * sum(bool(j.get("phd_required")) for j in group) / len(group)) if group else 0,
            "orgs": Counter(j.get("org") for j in group).most_common(8),
        }

    return {
        "postings_with_descriptions": len(jobs),
        "overall": block(jobs),
        "families": {name: block(group) for name, group in
                     sorted(by_family.items(), key=lambda kv: -len(kv[1]))},
    }


def to_markdown(summary, generated_at):
    lines = [f"# What the collected postings ask for", "",
             f"Based on {summary['postings_with_descriptions']} postings with descriptions "
             f"(collection run {generated_at}).", ""]

    def section(title, b, top=15):
        lines.append(f"## {title} ({b['postings']} postings)")
        y, p = b["years_required"], b["salary_max"]
        bits = []
        if y["median"] is not None:
            bits.append(f"median experience asked: {y['median']} yrs ({y['share_0_to_2']}% ask 2 or fewer; "
                        f"{y['postings_stating']} state a number)")
        if p["median"] is not None:
            bits.append(f"median top of pay range: ${int(p['median']):,} ({p['postings_stating']} list pay)")
        bits.append(f"PhD required: {b['phd_required_pct']}%")
        lines.append("; ".join(bits))
        lines.append("")
        lines.append("| Mentioned | Share |")
        lines.append("|---|---|")
        for term, pct in list(b["term_share_pct"].items())[:top]:
            lines.append(f"| {term} | {pct}% |")
        if b["orgs"]:
            lines.append("")
            lines.append("Most postings from: " + ", ".join(f"{o} ({n})" for o, n in b["orgs"]))
        lines.append("")

    section("All postings", summary["overall"], top=30)
    for name, b in summary["families"].items():
        section(name, b)
    lines.append("Shares count a posting once if its title or first 2,500 characters of description "
                 "mention the term. Mentions include both required and preferred qualifications.")
    return "\n".join(lines) + "\n"


def main():
    feed = json.loads((DATA / "jobs.json").read_text())
    summary = summarize(feed.get("jobs", []))
    summary["generated_at"] = feed.get("generated_at")
    (DATA / "analysis.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    (DATA / "analysis.md").write_text(to_markdown(summary, feed.get("generated_at")))
    print(f"Analyzed {summary['postings_with_descriptions']} postings into {len(summary['families'])} role families.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
