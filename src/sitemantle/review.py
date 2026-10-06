"""Readable readiness observations and comparisons, using saved crawl evidence."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from .evidence import capture_quality, missing_content, selected_data
from .extract import index_signals
from .network import RobotsRules, normalize_url, origin, utcnow


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_pages(folder):
    return [
        json.loads(line)
        for line in (Path(folder) / "pages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save_report(out, name, result, lines):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / (name + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / (name + ".md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def readiness(pages, summary, robots, sitemaps):
    entries = []
    listed = {r["url"] for r in sitemaps.get("urls", [])}
    for page in pages:
        data, representation = selected_data(page)
        quality = capture_quality(page)
        url = page.get("final_url") or page["url"]
        observations = []

        def add(code, observation, why, action):
            if (
                code in {"main_content_empty", "title_missing", "h1_missing", "schema_review"}
                and not quality["absence_supported"]
            ):
                return
            observations.append(
                {
                    "code": code,
                    "observation": observation,
                    "why_it_matters": why,
                    "next_step": action,
                }
            )

        if page.get("error") or not 200 <= page.get("status", 0) < 300 or not data:
            add(
                "access_incomplete",
                "We did not capture usable page content.",
                "We cannot assess an unread page's answers or trust signals.",
                "Check access.json and the response, then repeat this page when access is available.",
            )
        else:
            if quality["limits"]:
                add(
                    "capture_incomplete",
                    "; ".join(quality["limits"]),
                    "A partial capture cannot establish missing answers or markup.",
                    "Resolve the recorded capture limitation and recapture this URL.",
                )
            missing = missing_content(page)
            if missing:
                add(
                    "missing_content_candidate",
                    "HTTP success returned a missing-content screen candidate.",
                    "A successful status alone does not establish that the intended page exists.",
                    "Inspect the saved text, repair referring links and return an appropriate status for missing pages.",
                )
            if not data.get("main_text"):
                add(
                    "main_content_empty",
                    "The captured page has no main text.",
                    "Important answers may be unavailable in this representation.",
                    "Inspect rendered HTML and dependency errors; provide accessible text for essential information.",
                )
            raw = page.get("data") or {}
            if (
                representation == "rendered"
                and data.get("word_count", 0) > raw.get("word_count", 0) + 50
            ):
                add(
                    "javascript_content",
                    "Substantial text arrived after JavaScript ran.",
                    "Different crawlers may see different content.",
                    "Compare the raw response with the full page; consider server-rendering important answers and links.",
                )
            if not data.get("title"):
                add(
                    "title_missing",
                    "The captured page has no title.",
                    "Its topic is harder to identify in search results and browser tabs.",
                    "Write a descriptive title matching this page's purpose.",
                )
            if not data.get("headings", {}).get("h1"):
                add(
                    "h1_missing",
                    "We found no H1 in the captured content.",
                    "A clear page heading can help visitors understand its purpose.",
                    "Review the visible structure and add a useful primary heading where appropriate.",
                )
            if data.get("jsonld_errors"):
                add(
                    "schema_syntax",
                    "At least one JSON-LD block could not be parsed.",
                    "Malformed structured data cannot reliably describe the page.",
                    "Fix the recorded syntax errors, then validate the intended schema against visible facts.",
                )
            if not data.get("schema_types") and not data.get("microdata_types"):
                add(
                    "schema_review",
                    "No JSON-LD or microdata types were observed.",
                    "Structured data may clarify real entities, but its absence does not establish an AI visibility problem.",
                    "Review relevant markup for this page; add only types and facts it actually supports.",
                )
        raw_signals = index_signals(
            page.get("data") or {}, page.get("headers") or {}, page.get("status", 0), url
        )
        selected_signals = index_signals(
            data, page.get("headers") or {}, page.get("status", 0), url
        )
        if (
            raw_signals["googlebot_noindex_observed"]
            or selected_signals["googlebot_noindex_observed"]
        ):
            add(
                "noindex_observed",
                "A noindex instruction was observed in the response or captured DOM.",
                "This can prevent the page from appearing in Google Search, including its AI features.",
                "Confirm whether the page should be public in search before changing this instruction.",
            )
        controls = (
            selected_signals["generic_meta"]
            + selected_signals["googlebot_meta"]
            + [selected_signals["x_robots_tag"]]
        )
        # Preserve exact header text; agent-specific headers need individual interpretation.
        snippet_controls = [
            v for v in controls if re.search(r"nosnippet|max-snippet\s*:\s*0(?:\D|$)", v, re.I)
        ]
        evidence = robots.get(origin(url))
        policies = {}
        for agent in ("Googlebot", "Bingbot"):
            policies[agent] = (
                None
                if not evidence or evidence.get("blocked")
                else RobotsRules(evidence.get("text", ""), agent=agent).allowed(url)
            )
        if any(v is False for v in policies.values()):
            add(
                "robots_restriction",
                "The saved robots file restricts this path for a checked search crawler.",
                "That crawler may be unable to discover current page content.",
                "Review the exact rule and its purpose; changing our crawl override does not change search-engine access.",
            )
        entries.append(
            {
                "url": page["url"],
                "final_url": url,
                "representation": representation,
                "status": page.get("status"),
                "main_words": data.get("word_count", 0),
                "body_words": data.get("body_word_count", 0),
                "in_observed_sitemap": page["url"] in listed or url in listed,
                "schema_types": data.get("schema_types", []),
                "microdata_types": data.get("microdata_types", []),
                "schema_errors": data.get("jsonld_errors", []),
                "robots_path_permissions": policies,
                "snippet_controls_to_review": snippet_controls,
                "index_signals_http": raw_signals,
                "index_signals_selected": selected_signals,
                "observations": observations,
            }
        )
    return {
        "schema_version": 1,
        "checked_at": summary.get("exported_at") or utcnow(),
        "seed": summary.get("seed"),
        "coverage_limited": summary.get("coverage_limited", True),
        "sitemap_urls_observed": len(listed),
        "sitemap_fetch_failures": summary.get("sitemap_fetch_failures", 0),
        "pages": entries,
        "not_measured": [
            "actual indexing",
            "AI answer inclusion",
            "competitor rankings",
            "independent reputation",
            "schema semantic validity",
        ],
        "interpretation": "These are access, text and markup observations. They do not measure an engine's trust, indexing or citations.",
    }


def export_readiness(out, pages=None, summary=None, robots=None, sitemaps=None):
    out = Path(out)
    result = readiness(
        pages if pages is not None else read_pages(out),
        summary if summary is not None else read_json(out / "summary.json"),
        robots if robots is not None else read_json(out / "robots.json"),
        sitemaps if sitemaps is not None else read_json(out / "sitemaps.json"),
    )
    lines = [
        "# Your website's search and answer readiness",
        "",
        "Here is what we could read, what needs attention, and what to do next.",
        "",
        result["interpretation"],
        "",
        f"Sitemap URLs observed: {result['sitemap_urls_observed']}. Coverage limited: {result['coverage_limited']}.",
        "",
        "Start with access problems on important pages, then review the content and proof that answer your customers' questions.",
    ]
    for p in result["pages"]:
        lines += [
            "",
            "## " + p["url"],
            "",
            f"Captured {p['main_words']} main-content words using {p['representation']}. Schema types: {', '.join(p['schema_types']) or 'none observed'}.",
        ]
        for item in p["observations"]:
            lines += [
                "",
                item["observation"]
                + " "
                + item["why_it_matters"]
                + " **Next:** "
                + item["next_step"],
            ]
        if not p["observations"]:
            lines += [
                "",
                "No issue was flagged by these limited checks. Review answer quality, entity facts and independent proof separately.",
            ]
    return save_report(out, "readiness", result, lines)



# --- SiteMantle Development Director ---
# Converts observed audit findings into deterministic implementation instructions.
# It does not edit the target website, invent business facts, or claim that a fix
# guarantees ranking, AI citation, AdSense approval, or legal compliance.
DIRECTOR_PRIORITY = {
    "access": 0,
    "crawler_access": 0,
    "privacy": 1,
    "content_policy": 1,
    "publisher_trust": 1,
    "navigation": 2,
    "inventory_value": 2,
    "ad_placement": 2,
    "ad_behavior": 2,
    "ad_density": 2,
    "originality": 3,
    "entity_clarity": 4,
    "answer_extractability": 5,
    "citation_worthiness": 5,
    "conversation_coverage": 6,
    "agent_readability": 7,
    "multimodal": 8,
}
DIRECTOR_SEVERITY = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "review": 4}


def _director_level(severity: str) -> str:
    if severity in {"blocker", "high"}:
        return "must_fix"
    if severity == "medium":
        return "should_fix"
    return "verify"


def _director_gate(directives: list[dict]) -> str:
    severities = {d["severity"] for d in directives}
    if "blocker" in severities:
        return "STOP_BEFORE_LAUNCH"
    if "high" in severities:
        return "FIX_REQUIRED"
    if "medium" in severities:
        return "IMPROVE_BEFORE_LAUNCH"
    if severities:
        return "REVIEW_RECOMMENDED"
    return "READY_FOR_MANUAL_REVIEW"


def _director_acceptance(source: str, code: str, url: str | None) -> str:
    command = (
        "sitemantle modern-audit --out <crawl-folder>"
        if source == "modern_search"
        else "sitemantle adsense-audit --out <crawl-folder>"
    )
    target = f" for {url}" if url else ""
    return f"Re-crawl after the change, run `{command}`, and confirm `{code}` is no longer reported{target}."


def development_director(
    pages: list[dict],
    summary: dict,
    robots: dict,
    html_by_url: dict[str, str] | None = None,
    phase: str = "build",
    goal: str = "",
) -> dict:
    """Turn SiteMantle evidence into ordered website-development instructions."""
    if phase not in {"build", "prelaunch", "repair"}:
        raise ValueError("phase must be build, prelaunch or repair")
    modern = modern_audit(pages, summary, robots)
    adsense = adsense_audit(pages, summary, html_by_url or {})
    directives = []

    def collect(source, issue, url=None):
        directives.append(
            {
                "source": source,
                "url": url,
                "area": issue["area"],
                "code": issue["code"],
                "severity": issue["severity"],
                "level": _director_level(issue["severity"]),
                "evidence": issue["evidence"],
                "instruction": issue["action"],
                "acceptance_check": _director_acceptance(source, issue["code"], url),
                "policy_backed": issue.get("policy_backed") if source == "adsense" else None,
            }
        )

    for page in modern["pages"]:
        for issue in page["issues"]:
            collect("modern_search", issue, page["url"])
    for issue in adsense["site_issues"]:
        collect("adsense", issue, None)
    for page in adsense["pages"]:
        for issue in page["issues"]:
            collect("adsense", issue, page["url"])

    # A single underlying observation can appear in several audits. Preserve different
    # rule codes, but collapse exact duplicate instructions for the same page/code/source.
    unique = {}
    for item in directives:
        key = (item["source"], item["url"], item["code"])
        unique.setdefault(key, item)
    directives = sorted(
        unique.values(),
        key=lambda d: (
            DIRECTOR_SEVERITY.get(d["severity"], 9),
            DIRECTOR_PRIORITY.get(d["area"], 99),
            d["url"] or "",
            d["code"],
        ),
    )

    sitewide = [d for d in directives if not d["url"]]
    by_page = {}
    roles = {p["url"]: p.get("role") for p in modern["pages"]}
    for item in directives:
        if not item["url"]:
            continue
        by_page.setdefault(item["url"], []).append(item)
    page_plans = [
        {
            "url": url,
            "role": roles.get(url),
            "gate": _director_gate(items),
            "directives": items,
        }
        for url, items in sorted(by_page.items())
    ]

    counts = Counter(d["level"] for d in directives)
    severity_counts = Counter(d["severity"] for d in directives)
    gate = _director_gate(directives)
    constraints = [
        "Preserve the site's existing visual identity and working functionality unless a reported issue requires a scoped change.",
        "Do not invent reviews, ratings, credentials, authors, offices, statistics, research, customers, prices or other business facts.",
        "Do not create filler, doorway or near-duplicate pages simply to satisfy SEO, AI-search or monetization checks.",
        "Do not remove robots/noindex restrictions unless their original intent has been confirmed.",
        "Keep Google-served ads visually and semantically separate from download, play, navigation, conversion and other primary controls.",
        "Treat SiteMantle scores as checklists, not ranking, citation or AdSense-approval probabilities.",
        "For AdSense-first builds, keep public monetized pages separate from private processing/result/download states and preserve the AdSense Guard Advanced build standard.",
        "Implement in small reviewable batches and re-crawl before marking a directive complete.",
    ]
    if phase == "prelaunch":
        constraints.append("Do not recommend launch while any blocker or high-severity directive remains unresolved or explicitly accepted as a known risk.")
    if phase == "repair":
        constraints.append("Prefer minimal fixes to the existing implementation; avoid redesigning unrelated sections while repairing audit findings.")

    top = [d for d in directives if d["level"] == "must_fix"][:12]
    prompt_lines = [
        "You are implementing a SiteMantle website-development plan.",
        f"Phase: {phase}.",
        f"Goal: {goal.strip() or 'Improve the audited website while preserving verified business facts and existing design intent.'}",
        f"Release gate: {gate}.",
        "",
        "Non-negotiable constraints:",
        *[f"- {x}" for x in constraints],
        "",
        "Implement these highest-priority directives first:",
    ]
    if top:
        for index, item in enumerate(top, 1):
            where = item["url"] or "site-wide"
            prompt_lines.append(
                f"{index}. [{item['severity'].upper()}] {where} — {item['instruction']} "
                f"Evidence: {item['evidence']}"
            )
    else:
        prompt_lines.append("- No blocker/high directive was observed. Work through should-fix items, then verification items.")
    prompt_lines += [
        "",
        "After each batch, preserve unrelated code, run the project's normal tests/build, re-crawl the affected pages, and rerun the relevant SiteMantle audit. Do not mark a finding resolved until the acceptance check passes.",
    ]

    return {
        "schema_version": 1,
        "product": "SiteMantle",
        "module": "Development Director",
        "checked_at": summary.get("exported_at") or utcnow(),
        "seed": summary.get("seed"),
        "phase": phase,
        "goal": goal.strip(),
        "release_gate": gate,
        "coverage_limited": summary.get("coverage_limited", True),
        "pages_analyzed": len(pages),
        "directive_summary": {
            "total": len(directives),
            "by_level": dict(counts),
            "by_severity": dict(severity_counts),
        },
        "implementation_sequence": [
            "Resolve access/crawler blockers and site-wide policy blockers.",
            "Resolve AdSense inventory, placement, originality and content-policy risks.",
            "Strengthen entity clarity, direct-answer structure and evidence/source context.",
            "Cover real decision questions, agent-readable interactions and multimodal gaps.",
            "Re-crawl changed pages and rerun SiteMantle before launch/review.",
        ],
        "constraints": constraints,
        "sitewide_directives": sitewide,
        "page_plans": page_plans,
        "all_directives": directives,
        "llm_implementation_brief": "\n".join(prompt_lines),
        "inputs": {
            "modern_search_issue_count": modern["issue_summary"]["total"],
            "adsense_issue_count": adsense["issue_summary"]["total"],
            "adsense_readiness": adsense["readiness"]["classification"],
        },
        "not_measured": sorted(set(modern["not_measured"] + adsense["not_measured"])),
        "interpretation": "Development Director converts SiteMantle's observed audit evidence into ordered implementation instructions. It does not edit the website itself and does not guarantee search rankings, AI citations or AdSense approval.",
    }


def export_development_director(
    out: Path | str,
    phase: str = "build",
    goal: str = "",
):
    out = Path(out)
    pages = read_pages(out)
    summary = read_json(out / "summary.json")
    robots = read_json(out / "robots.json")
    html_by_url = {
        (p.get("final_url") or p.get("url", "")): _captured_html_for_adsense(out, p)
        for p in pages
    }
    result = development_director(pages, summary, robots, html_by_url, phase, goal)
    lines = [
        "# SiteMantle Development Director",
        "",
        result["interpretation"],
        "",
        f"**Phase:** {result['phase']}",
        f"**Release gate:** {result['release_gate']}",
        f"**Directives:** {result['directive_summary']['total']}",
        "",
        "## Implementation sequence",
        "",
        *[f"{i}. {step}" for i, step in enumerate(result["implementation_sequence"], 1)],
        "",
        "## Non-negotiable constraints",
        "",
        *["- " + x for x in result["constraints"]],
        "",
    ]
    if result["sitewide_directives"]:
        lines += ["## Site-wide directives", ""]
        for item in result["sitewide_directives"]:
            lines += [
                f"### {item['level'].upper()} · {item['severity'].upper()} · {item['code']}",
                "",
                item["evidence"],
                "",
                "**Implement:** " + item["instruction"],
                "",
                "**Accept when:** " + item["acceptance_check"],
                "",
            ]
    lines += ["## Page plans", ""]
    if not result["page_plans"]:
        lines.append("No page-specific directive was generated by the current bounded audits.")
    for page in result["page_plans"]:
        lines += [f"### {page['url']}", "", f"**Gate:** {page['gate']}", ""]
        for item in page["directives"]:
            lines += [
                f"- **{item['level']} / {item['severity']} / {item['area']}** — {item['instruction']}  ",
                f"  Evidence: {item['evidence']}  ",
                f"  Acceptance: {item['acceptance_check']}",
            ]
        lines.append("")
    lines += [
        "## Copy-paste implementation brief",
        "",
        "```text",
        result["llm_implementation_brief"],
        "```",
        "",
        "## Important limits",
        "",
        *["- " + x for x in result["not_measured"]],
    ]
    return save_report(out, "development-director", result, lines)

def page_snapshot(page):
    data, representation = selected_data(page)
    signals = index_signals(
        data, page.get("headers") or {}, page.get("status", 0), page.get("final_url", page["url"])
    )
    return {
        "status": page.get("status"),
        "error": page.get("error", ""),
        "final_url": page.get("final_url"),
        "representation": representation,
        "title": data.get("title"),
        "description": data.get("meta_description"),
        "h1": data.get("headings", {}).get("h1", []),
        "content_hash": data.get("content_sha256"),
        "canonical": data.get("canonical", []),
        "noindex": signals["googlebot_noindex_observed"],
        "schema_types": data.get("schema_types", []),
        "schema_errors": data.get("jsonld_errors", []),
        "render_error": (page.get("rendered") or {}).get("error", ""),
    }


def compare(before, after, out):
    a, b = Path(before), Path(after)
    sa, sb = read_json(a / "summary.json"), read_json(b / "summary.json")
    if sa["seed"] != sb["seed"]:
        raise ValueError(
            "Snapshot comparison needs the same seed URL. Compare competitors in the SEO workflow."
        )
    left, right = ({p["url"]: page_snapshot(p) for p in read_pages(d)} for d in (a, b))
    changed = []
    for url in sorted(left.keys() & right.keys()):
        fields = {
            k: {"before": left[url][k], "after": right[url][k]}
            for k in left[url]
            if left[url][k] != right[url][k]
        }
        if fields:
            changed.append({"url": url, "fields": fields})
    old_issues, new_issues = (
        {(r["url"], r["code"]) for r in read_json(d / "issues.json")} for d in (a, b)
    )
    # A disappearing URL or failed observation must never count as a resolved issue.
    comparable = {
        u
        for u in left.keys() & right.keys()
        if left[u]["content_hash"]
        and right[u]["content_hash"]
        and not left[u]["error"]
        and not right[u]["error"]
        and left[u]["status"] == right[u]["status"] == 200
        and left[u]["representation"] == right[u]["representation"]
        and not left[u]["render_error"]
        and not right[u]["render_error"]
    }
    resolved = sorted(i for i in old_issues - new_issues if i[0] in comparable)
    result = {
        "checked_at": utcnow(),
        "seed": sa["seed"],
        "before_at": sa.get("exported_at"),
        "after_at": sb.get("exported_at"),
        "coverage_limited": bool(sa.get("coverage_limited") or sb.get("coverage_limited")),
        "configuration_changed": sa.get("configuration") != sb.get("configuration"),
        "changed_pages": changed,
        "newly_observed_urls": sorted(right.keys() - left.keys()),
        "not_reobserved_urls": sorted(left.keys() - right.keys()),
        "new_findings": [{"url": u, "code": c} for u, c in sorted(new_issues - old_issues)],
        "findings_no_longer_observed_on_comparable_pages": [
            {"url": u, "code": c} for u, c in resolved
        ],
        "note": "Not reobserved does not mean deleted. Findings no longer observed need acceptance checks; crawl scope and rendering can affect comparison.",
    }
    lines = [
        "# What changed since the last review",
        "",
        f"{len(changed)} pages changed; {len(result['new_findings'])} new findings; {len(resolved)} findings no longer observed on comparable pages.",
        "",
        result["note"],
        "",
        f"Coverage limited: {result['coverage_limited']}. Configuration changed: {result['configuration_changed']}.",
    ]
    for row in changed:
        lines += ["", "- " + row["url"] + ": " + ", ".join(row["fields"])]
    return save_report(out, "comparison", result, lines)


# --- SiteMantle modern-search executable audit layer ---
AI_CRAWLERS = (
    ("OAI-SearchBot", "OpenAI search discovery"),
    ("GPTBot", "OpenAI model crawler"),
    ("ChatGPT-User", "OpenAI user-triggered fetches"),
    ("ClaudeBot", "Anthropic crawler"),
    ("PerplexityBot", "Perplexity crawler"),
    ("Googlebot", "Google search crawler"),
    ("Bingbot", "Microsoft/Bing crawler"),
)

ENTITY_SCHEMA = {
    "Organization", "LocalBusiness", "Corporation", "Person", "Product", "Service",
    "WebSite", "WebPage", "Article", "NewsArticle", "ProfilePage", "AboutPage",
}
SOURCE_HINTS = re.compile(
    r"\b(source|sources|reference|references|methodology|data|dataset|research|study|survey|"
    r"case study|case studies|according to|evidence|report|measured|sample|method)\b",
    re.I,
)
DIRECT_ANSWER_HINTS = re.compile(
    r"\b(what is|how to|how does|why|cost|price|pricing|compare|comparison|versus|\bvs\b|"
    r"pros and cons|benefits|steps|process|risk|limitations?|faq|frequently asked)\b",
    re.I,
)
TRUST_HINTS = re.compile(
    r"\b(author|reviewed by|expert|founder|credentials?|certified|experience|case study|"
    r"methodology|contact|about|privacy|terms|editorial policy)\b",
    re.I,
)


def _words(text: str) -> int:
    return len((text or "").split())


def _page_role(url: str) -> str:
    path = urlsplit(url).path.lower().strip("/")
    if not path:
        return "home"
    if any(x in path for x in ("about", "company", "team", "author", "founder")):
        return "entity"
    if any(x in path for x in ("service", "product", "pricing", "plans")):
        return "commercial"
    if any(x in path for x in ("blog", "guide", "article", "learn", "resources")):
        return "content"
    return "other"


def _robot_permissions(robots: dict, url: str) -> dict:
    evidence = robots.get(origin(url))
    result = {}
    for agent, purpose in AI_CRAWLERS:
        if not evidence:
            allowed = None
            status = "not_observed"
        elif evidence.get("blocked"):
            allowed = None
            status = "robots_unavailable"
        else:
            allowed = RobotsRules(evidence.get("text", ""), agent=agent).allowed(url)
            status = "allowed" if allowed else "blocked"
        result[agent] = {"status": status, "allowed": allowed, "purpose": purpose}
    return result


def _analyze_page(page: dict, robots: dict) -> dict:
    data, representation = selected_data(page)
    quality = capture_quality(page)
    url = page.get("final_url") or page["url"]
    issues = []

    def add(area, code, severity, evidence, action):
        issues.append({
            "area": area,
            "code": code,
            "severity": severity,
            "evidence": evidence,
            "action": action,
        })

    if page.get("error") or not 200 <= page.get("status", 0) < 300 or not data:
        add("access", "page_unusable", "high", "No usable page content was captured.",
            "Resolve the recorded access/capture problem before interpreting modern-search readiness.")
        return {
            "url": url, "role": _page_role(url), "representation": representation,
            "crawler_access": _robot_permissions(robots, url), "issues": issues,
            "signals": {},
        }

    text = data.get("main_text") or data.get("text") or ""
    headings = [h for level in data.get("headings", {}).values() for h in level]
    schema = set(data.get("schema_types", []))
    links = data.get("links", [])
    images = data.get("images", [])
    forms = data.get("forms", [])
    role = _page_role(url)

    crawler_access = _robot_permissions(robots, url)
    blocked = [a for a, v in crawler_access.items() if v["allowed"] is False]
    if blocked:
        add("crawler_access", "ai_crawler_blocked", "high",
            "Blocked for: " + ", ".join(blocked) + ".",
            "Review the exact robots.txt rules and confirm each restriction is intentional; do not remove deliberate controls only to improve a score.")

    wc = data.get("word_count", _words(text))
    question_headings = sum(bool(DIRECT_ANSWER_HINTS.search(h)) for h in headings)
    if wc >= 180 and question_headings == 0 and role in {"commercial", "content", "other"}:
        add("answer_extractability", "weak_answer_structure", "medium",
            "The page has substantial text but no clearly question/decision-oriented heading was observed.",
            "Add useful direct-answer sections for real user questions such as process, cost, comparisons, risks or selection criteria where they belong.")
    if wc < 120 and role in {"commercial", "content"} and quality.get("absence_supported"):
        add("answer_extractability", "limited_explanatory_content", "medium",
            f"Only {wc} main-content words were captured on a {role} page.",
            "Add the information a visitor needs to make a decision; do not pad the page to meet a word count.")

    entity_types = sorted(schema & ENTITY_SCHEMA)
    if role in {"home", "entity", "commercial"} and not entity_types and quality.get("absence_supported"):
        add("entity_clarity", "entity_schema_not_observed", "medium",
            "No relevant entity-oriented structured-data type was observed.",
            "Review whether Organization/Person/Product/Service or another accurate Schema.org type can describe visible facts on this page.")
    if role in {"home", "entity"} and not TRUST_HINTS.search(text):
        add("entity_clarity", "weak_identity_proof", "medium",
            "Few obvious identity/expertise/trust terms were observed in the captured main content.",
            "Make the organization/person, expertise, contact path and real proof easy to identify without inventing credentials.")

    external = [l for l in links if urlsplit(l.get("url", "")).netloc and urlsplit(l.get("url", "")).netloc != urlsplit(url).netloc]
    numbers = re.findall(r"(?<!\w)(?:\d{1,3}(?:[,.]\d{3})*|\d+(?:\.\d+)?)%?", text)
    source_hints = bool(SOURCE_HINTS.search(text))
    if role == "content" and wc >= 300 and (numbers or re.search(r"\b(study|research|survey|data)\b", text, re.I)) and not (source_hints and external):
        add("citation_worthiness", "claims_need_source_context", "high",
            "The page contains quantitative/research-style claims but weak source context or outbound evidence was observed.",
            "Support important factual claims with attributable sources, methodology, first-hand evidence or clearly stated limitations.")
    if role in {"content", "commercial"} and wc >= 250 and not source_hints:
        add("citation_worthiness", "little_original_evidence_signposting", "low",
            "No obvious methodology, evidence, research, case-study or source language was observed.",
            "Where appropriate, add original examples, methodology, measurements, case studies or cited evidence that make the page worth referencing.")

    decision_terms = {
        "cost": bool(re.search(r"\b(cost|price|pricing|fee)\b", text, re.I)),
        "process": bool(re.search(r"\b(process|steps?|how it works|procedure)\b", text, re.I)),
        "comparison": bool(re.search(r"\b(compare|comparison|versus|\bvs\b|alternative)\b", text, re.I)),
        "limitations": bool(re.search(r"\b(risk|limitation|not suitable|drawback|constraint)\b", text, re.I)),
    }
    if role == "commercial" and sum(decision_terms.values()) < 2:
        add("conversation_coverage", "decision_questions_undercovered", "medium",
            "Fewer than two common decision-question categories were detected (cost, process, comparison, limitations).",
            "Answer the real follow-up questions prospects ask before choosing this product/service; include only categories relevant to the offer.")

    if forms and all(f.get("fields", 0) > 0 for f in forms):
        add("agent_readability", "form_semantics_need_verification", "low",
            f"{len(forms)} form(s) were observed, but the current capture does not prove field labels or accessible names.",
            "Verify every form control has a programmatic label, meaningful name/type and an unambiguous action so humans and agents can interpret it.")

    missing_alt = sum(1 for i in images if not i.get("alt_present") or not (i.get("alt") or "").strip())
    missing_dimensions = sum(1 for i in images if not i.get("width") or not i.get("height"))
    if images and missing_alt:
        add("multimodal", "images_without_useful_alt", "medium",
            f"{missing_alt} of {len(images)} image(s) lack an observed non-empty alt attribute.",
            "Add concise contextual alternatives for informative images; use empty alt for intentionally decorative images rather than keyword stuffing.")
    if images and missing_dimensions:
        add("multimodal", "image_dimensions_missing", "low",
            f"{missing_dimensions} of {len(images)} image(s) lack explicit width/height attributes.",
            "Where compatible with the design, provide intrinsic dimensions and keep image context close to the related entity/product/content.")

    return {
        "url": url,
        "role": role,
        "representation": representation,
        "crawler_access": crawler_access,
        "signals": {
            "main_words": wc,
            "question_or_decision_headings": question_headings,
            "entity_schema_types": entity_types,
            "external_links": len(external),
            "images": len(images),
            "forms": len(forms),
            "decision_question_categories": decision_terms,
        },
        "issues": issues,
    }


def modern_audit(pages: list[dict], summary: dict, robots: dict) -> dict:
    analyzed = [_analyze_page(p, robots) for p in pages]
    issues = [i for p in analyzed for i in p["issues"]]
    by_area = Counter(i["area"] for i in issues)
    by_severity = Counter(i["severity"] for i in issues)
    crawler_totals = {}
    for agent, purpose in AI_CRAWLERS:
        statuses = Counter(p["crawler_access"][agent]["status"] for p in analyzed)
        crawler_totals[agent] = {"purpose": purpose, "statuses": dict(statuses)}
    return {
        "schema_version": 1,
        "product": "SiteMantle",
        "checked_at": summary.get("exported_at") or utcnow(),
        "seed": summary.get("seed"),
        "coverage_limited": summary.get("coverage_limited", True),
        "pages_analyzed": len(analyzed),
        "issue_summary": {"total": len(issues), "by_area": dict(by_area), "by_severity": dict(by_severity)},
        "crawler_access_summary": crawler_totals,
        "pages": analyzed,
        "not_measured": [
            "actual AI/search inclusion or ranking",
            "actual brand mentions or citations in generated answers",
            "search-engine trust or approval",
            "complete accessibility semantics",
            "complete Schema.org semantic validity",
        ],
        "interpretation": "SiteMantle checks observable website signals that affect modern search and machine understanding. It reports readiness and gaps, not guaranteed rankings, citations or AI visibility.",
    }


def export_modern_audit(out: Path | str):
    out = Path(out)
    pages = read_pages(out)
    summary = read_json(out / "summary.json")
    robots = read_json(out / "robots.json")
    result = modern_audit(pages, summary, robots)
    lines = [
        "# SiteMantle modern search audit", "", result["interpretation"], "",
        f"Pages analyzed: {result['pages_analyzed']}. Issues: {result['issue_summary']['total']}.", "",
        "## Priority findings", "",
    ]
    priority = sorted(
        [i | {"url": p["url"]} for p in result["pages"] for i in p["issues"]],
        key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3),
    )
    if not priority:
        lines.append("No issue was flagged by these bounded checks. This does not prove AI/search visibility.")
    for item in priority:
        lines += [
            f"### {item['severity'].upper()} · {item['area']} · {item['url']}", "",
            item["evidence"], "", "**Recommended action:** " + item["action"], "",
        ]
    return save_report(out, "modern-search", result, lines)



# --- SiteMantle AdSense Guard ---
# These checks are evidence-based readiness heuristics grounded in Google Publisher/AdSense
# policies. They do NOT estimate Google's private approval decision or replace manual review.
ADSENSE_POLICY_REFERENCES = (
    {
        "name": "Google Publisher Policies",
        "url": "https://support.google.com/adsense/answer/10502938",
        "covers": "inventory value, ad behavior, content and privacy policy requirements",
    },
    {
        "name": "Screens without publisher content",
        "url": "https://support.google.com/publisherpolicies/answer/11112688",
        "covers": "low/no publisher content, under-construction and behavioral/dead-end screens",
    },
    {
        "name": "Replicated content",
        "url": "https://support.google.com/publisherpolicies/answer/11190248",
        "covers": "copied/embedded/automatically generated content without added value or review",
    },
    {
        "name": "More ads than publisher content",
        "url": "https://support.google.com/publisherpolicies/answer/11169917",
        "covers": "ad/promotional density versus publisher content",
    },
    {
        "name": "Privacy disclosures",
        "url": "https://support.google.com/publisherpolicies/answer/10437794",
        "covers": "privacy policy disclosures for Google products, cookies and identifiers",
    },
    {
        "name": "Ad placement policies",
        "url": "https://support.google.com/adsense/answer/1346295",
        "covers": "accidental clicks, misleading placement, pop-ups and interfering ads",
    },
    {
        "name": "Ads.txt guide",
        "url": "https://support.google.com/adsense/answer/12171612",
        "covers": "ads.txt is recommended but not mandatory",
    },
    {
        "name": "Make sure your site's pages are ready for AdSense",
        "url": "https://support.google.com/adsense/answer/7299563",
        "covers": "unique relevant content, clear navigation and useful user experience",
    },
    {
        "name": "Eligibility requirements for AdSense",
        "url": "https://support.google.com/adsense/answer/9724",
        "covers": "own high-quality original content, policy compliance and site ownership/control",
    },
    {
        "name": "Ads interfering with content or interactions",
        "url": "https://support.google.com/publisherpolicies/answer/11035030",
        "covers": "ads adjacent to navigation/actions, overlays and dead-end interaction traps",
    },
    {
        "name": "Required privacy content",
        "url": "https://support.google.com/adsense/answer/1348695",
        "covers": "Google/third-party advertising cookie disclosures and opt-out information",
    },
    {
        "name": "Unsupported languages",
        "url": "https://support.google.com/publisherpolicies/answer/10436912",
        "covers": "Google-served ads require content primarily in a supported publisher language",
    },
    {
        "name": "Google Publisher Policies — intellectual property",
        "url": "https://support.google.com/publisherpolicies/answer/10502938",
        "covers": "copyright infringement and other prohibited publisher content",
    },
)

ADSENSE_CTA_RE = re.compile(
    r"\b(download|play|next|previous|continue|start|open|install|get\s+file|convert|generate|submit)\b",
    re.I,
)
ADSENSE_DEAD_END_RE = re.compile(
    r"\b(under construction|coming soon|maintenance mode|page not found|404 error|"
    r"thank you for (?:your )?(?:submission|order|message)|nothing here|error occurred)\b",
    re.I,
)
ADSENSE_SENSITIVE_RE = re.compile(
    r"\b(casino|sports betting|porn(?:ography)?|escort service|buy cocaine|buy heroin|"
    r"counterfeit documents?|fake passport|explosive device|unlicensed firearm sales?)\b",
    re.I,
)

ADSENSE_PRIVATE_STATE_RE = re.compile(
    r"\b(processing|conversion complete|file ready|download your|preview your|preview file|"
    r"result ready|your result|upload complete|generating file|preparing download)\b",
    re.I,
)
ADSENSE_PRIVATE_PATH_SEGMENTS = {
    "processing", "process", "preview", "result", "results", "output", "complete",
    "completed", "success", "download-result", "download-file", "converted",
}
ADSENSE_DOWNLOADER_RE = re.compile(
    r"\b(youtube|tiktok|instagram|facebook|twitter|spotify|netflix|vimeo)\b.{0,80}"
    r"\b(download(?:er|ing)?|save video|save audio|mp3|mp4|rip(?:per|ping)?)\b|"
    r"\b(download(?:er|ing)?|save video|save audio|mp3|mp4|rip(?:per|ping)?)\b.{0,80}"
    r"\b(youtube|tiktok|instagram|facebook|twitter|spotify|netflix|vimeo)\b",
    re.I | re.S,
)
ADSENSE_SUPPORTED_LANGUAGE_CODES = {
    "ar", "bn", "bg", "ca", "zh", "hr", "cs", "da", "nl", "en", "et", "fil", "fi",
    "fr", "de", "el", "gu", "he", "hi", "hu", "id", "it", "ja", "kn", "ko", "lv",
    "lt", "ms", "ml", "mr", "no", "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl",
    "es", "sv", "ta", "te", "th", "tr", "uk", "ur", "vi",
}
ADSENSE_TOOL_SECTION_RE = re.compile(
    r"\b(how (?:it|this) works?|how to|supported|formats?|limitations?|privacy|security|"
    r"examples?|faq|frequently asked|why use|features?|troubleshoot|tips?)\b",
    re.I,
)


def _adsense_role(url: str, data: dict) -> str:
    path = urlsplit(url).path.lower().strip("/")
    title = (data.get("title") or "").lower()
    combined = path + " " + title
    if any(x in combined for x in ("privacy", "cookie-policy", "privacy-policy")):
        return "privacy"
    if any(x in combined for x in ("thank-you", "thank_you", "404", "error", "success")):
        return "dead_end_candidate"
    if any(x in combined for x in ("about", "contact", "terms", "disclaimer")):
        return "trust"
    if any(x in combined for x in ("blog", "article", "guide", "news", "learn")):
        return "content"
    if any(x in combined for x in ("tool", "convert", "compress", "calculator", "generator")):
        return "tool"
    return _page_role(url)


def _adsense_dom_signals(html: str, page_url: str) -> dict:
    """Inspect captured DOM text/markup for ad-placement signals without claiming layout proof."""
    if not html:
        return {
            "html_available": False,
            "google_ad_code_observed": False,
            "ad_units_observed": 0,
            "external_embeds": 0,
            "cta_near_ad_candidates": [],
            "fixed_or_overlay_ad_candidates": 0,
            "window_open_calls": 0,
            "cmp_signals_observed": False,
            "file_upload_inputs": 0,
            "misleading_ad_label_candidates": [],
        }
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    raw_lower = html.lower()
    google_markers = (
        "adsbygoogle",
        "pagead2.googlesyndication.com",
        "google_ad_client",
        "data-ad-client",
        "data-ad-slot",
        "doubleclick.net/pagead",
    )
    google_ad_code = any(m in raw_lower for m in google_markers)

    ad_nodes = []
    seen = set()
    selectors = (
        "ins.adsbygoogle",
        "[data-ad-slot]",
        "[data-ad-client]",
        "iframe[src*='googlesyndication']",
        "iframe[src*='doubleclick']",
        "[id^='google_ads_']",
    )
    for selector in selectors:
        for node in soup.select(selector):
            marker = id(node)
            if marker not in seen:
                seen.add(marker)
                ad_nodes.append(node)

    cta_candidates = []
    fixed_candidates = 0
    for node in ad_nodes:
        # Static DOM proximity cannot establish rendered pixel distance. We only retain
        # same-parent / adjacent-control candidates for manual visual verification.
        context_nodes = []
        if node.parent:
            context_nodes.extend(list(node.parent.find_all(["a", "button", "input"], limit=12)))
        for sibling in (node.previous_sibling, node.next_sibling):
            if getattr(sibling, "find_all", None):
                context_nodes.extend(sibling.find_all(["a", "button", "input"], limit=6))
        for control in context_nodes:
            label = " ".join(
                str(x or "")
                for x in (
                    control.get_text(" ", strip=True),
                    control.get("value"),
                    control.get("aria-label"),
                    control.get("title"),
                )
            ).strip()
            if label and ADSENSE_CTA_RE.search(label):
                value = re.sub(r"\s+", " ", label)[:120]
                if value not in cta_candidates:
                    cta_candidates.append(value)
        ancestor = node
        for _ in range(4):
            if ancestor is None:
                break
            style = str(ancestor.get("style", "")).lower()
            classes = " ".join(str(c).lower() for c in ancestor.get("class", []))
            ident = str(ancestor.get("id", "")).lower()
            if (
                re.search(r"position\s*:\s*(fixed|sticky)", style)
                or any(x in classes + " " + ident for x in ("overlay", "sticky-ad", "floating-ad", "fixed-ad"))
            ):
                fixed_candidates += 1
                break
            ancestor = ancestor.parent

    page_host = urlsplit(page_url).netloc.lower()
    external_embeds = 0
    for frame in soup.find_all("iframe", src=True):
        host = urlsplit(normalize_url(frame.get("src"), page_url, False) or "").netloc.lower()
        if host and host != page_host and not any(x in host for x in ("googlesyndication", "doubleclick")):
            external_embeds += 1

    window_open_calls = len(re.findall(r"\bwindow\.open\s*\(", html, re.I))
    cmp_signals = any(
        x in raw_lower
        for x in ("__tcfapi", "funding choices", "googlefc", "consentmanager", "cookiebot", "onetrust")
    )
    file_upload_inputs = len(soup.select('input[type="file"]'))
    misleading_labels = []
    allowed_ad_label = re.compile(r"^\s*(advertisements?|sponsored links?)\s*$", re.I)
    for node in ad_nodes:
        candidates = []
        prev = node.find_previous_sibling()
        if prev is not None:
            candidates.append(prev.get_text(" ", strip=True))
        if node.parent is not None:
            direct_text = " ".join(
                t.strip() for t in node.parent.find_all(string=True, recursive=False) if t.strip()
            )
            if direct_text:
                candidates.append(direct_text)
        for label in candidates:
            label = re.sub(r"\s+", " ", label).strip()[:120]
            if not label or allowed_ad_label.match(label):
                continue
            if ADSENSE_CTA_RE.search(label) or re.search(r"\b(recommended|resources?|links?)\b", label, re.I):
                if label not in misleading_labels:
                    misleading_labels.append(label)
    return {
        "html_available": True,
        "google_ad_code_observed": google_ad_code,
        "ad_units_observed": len(ad_nodes),
        "external_embeds": external_embeds,
        "cta_near_ad_candidates": cta_candidates[:12],
        "fixed_or_overlay_ad_candidates": fixed_candidates,
        "window_open_calls": window_open_calls,
        "cmp_signals_observed": cmp_signals,
        "file_upload_inputs": file_upload_inputs,
        "misleading_ad_label_candidates": misleading_labels[:12],
    }


def _adsense_text_shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(tokens) < size:
        return set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def _adsense_similarity(a: str, b: str) -> float:
    left = _adsense_text_shingles(a)
    right = _adsense_text_shingles(b)
    jaccard = (len(left & right) / len(left | right)) if left and right else 0.0
    # Sequence similarity catches lightly edited/extended copies that can look less similar
    # under set-based shingles. Bound text length because this runs pairwise within a sample.
    na = re.sub(r"\s+", " ", (a or "").lower()).strip()[:12000]
    nb = re.sub(r"\s+", " ", (b or "").lower()).strip()[:12000]
    length_ratio = min(len(na), len(nb)) / max(len(na), len(nb), 1)
    sequence = SequenceMatcher(None, na, nb, autojunk=False).ratio() if length_ratio >= 0.75 else 0.0
    return max(jaccard, sequence)


def _adsense_page_analysis(page: dict, dom: dict) -> dict:
    data, representation = selected_data(page)
    quality = capture_quality(page)
    url = page.get("final_url") or page.get("url", "")
    role = _adsense_role(url, data)
    issues = []

    def add(area, code, severity, evidence, action, policy_backed=True):
        issues.append(
            {
                "area": area,
                "code": code,
                "severity": severity,
                "evidence": evidence,
                "action": action,
                "policy_backed": policy_backed,
            }
        )

    if page.get("error") or not 200 <= page.get("status", 0) < 300 or not data:
        add(
            "site_completeness",
            "page_unusable",
            "high",
            "No usable publisher content was captured for this URL.",
            "Resolve the recorded fetch/render problem before monetization review; an unreadable page cannot be cleared by SiteMantle.",
            False,
        )
        return {
            "url": url,
            "role": role,
            "representation": representation,
            "publisher_content_words": 0,
            "ad_signals": dom,
            "issues": issues,
        }

    text = data.get("main_text") or data.get("text") or ""
    words = data.get("word_count", len(text.split()))
    title = data.get("title", "")
    forms = data.get("forms", [])
    links = data.get("links", [])
    scripts = data.get("script_count", 0)
    interactive = bool(forms) or scripts >= 2
    has_ad_code = bool(dom.get("google_ad_code_observed") or dom.get("ad_units_observed"))

    h1_text = " ".join(data.get("headings", {}).get("h1", []))
    path_segments = {x for x in urlsplit(url).path.lower().split("/") if x}
    state_text = " ".join((data.get("title", ""), h1_text, text[:1200]))
    private_processing_state = bool(path_segments & ADSENSE_PRIVATE_PATH_SEGMENTS) or bool(
        ADSENSE_PRIVATE_STATE_RE.search(state_text)
    )
    if private_processing_state and has_ad_code:
        add(
            "inventory_value",
            "ads_on_private_processing_or_result_state",
            "blocker",
            "Google ad code/ad units were observed on a page that looks like a private processing, preview, result or download state.",
            "Separate monetized public tool/help pages from user-specific upload processing, preview, result and download states. Keep Google-served ads off those private/behavioral states.",
        )

    declared_language = (data.get("language") or "").strip().lower().replace("_", "-")
    base_language = declared_language.split("-", 1)[0] if declared_language else ""
    if base_language and base_language not in ADSENSE_SUPPORTED_LANGUAGE_CODES:
        add(
            "content_policy",
            "unsupported_publisher_language_candidate",
            "high",
            f"The page declares language `{declared_language}`, which is not in SiteMantle's current Google publisher-language allowlist snapshot.",
            "Verify the page's primary language against Google's current supported publisher languages before placing Google ad code. Update SiteMantle's list when Google changes support.",
        )

    all_headings = " ".join(
        h for level in data.get("headings", {}).values() for h in level
    )
    if role == "tool" and not private_processing_state:
        section_hits = len(set(m.group(0).lower() for m in ADSENSE_TOOL_SECTION_RE.finditer(all_headings)))
        if words < 140 and section_hits < 2:
            add(
                "inventory_value",
                "public_tool_page_needs_more_publisher_value",
                "high",
                f"This public tool page has about {words} captured main-content words and only {section_hits} explanatory/help section signal(s).",
                "Keep the working tool, but add genuinely useful original publisher content around it: what it does, how to use it, limitations, supported inputs/outputs, safety/privacy where relevant, examples and real FAQs. Do not add filler merely to hit a word count.",
                False,
            )

    downloader_identity = (urlsplit(url).path + " " + data.get("title", "") + " " + h1_text)[:3000]
    if ADSENSE_DOWNLOADER_RE.search(downloader_identity):
        add(
            "content_policy",
            "third_party_media_downloader_rights_risk",
            "high",
            "The page appears to offer downloading/ripping of media from a named third-party platform.",
            "For an AdSense-first product, exclude this feature unless you can document a lawful rights model and platform/provider authorization. Google Publisher Policies prohibit copyright infringement; SiteMantle does not assume every downloader is unlawful, so this remains a rights-review warning.",
            False,
        )

    if dom.get("misleading_ad_label_candidates"):
        labels = ", ".join(dom["misleading_ad_label_candidates"][:6])
        add(
            "ad_placement",
            "ad_may_be_mistaken_for_site_control",
            "blocker",
            "Ad-unit context contains control/navigation-like labeling such as: " + labels + ".",
            "Do not label or style ads so they can be mistaken for download, navigation, recommended-resource or other site controls. Use only clear ad labeling and visually separate ads from actions.",
        )

    headline = (title + " " + h1_text).strip()
    strong_dead_end = re.compile(
        r"\b(under construction|maintenance mode|page not found|404 error|nothing here|error occurred)\b",
        re.I,
    )
    # Navigation can legitimately link to a page named "Coming soon" or "Thank you".
    # Treat those softer phrases as page-state evidence only when they occur in the URL/title/H1,
    # while strong error/construction markers may also be recognized on a short page body.
    soft_headline = bool(re.search(r"\b(coming soon|thank you)\b", headline, re.I))
    body_dead_end = words <= 100 and bool(strong_dead_end.search((headline + " " + text)[:3000]))
    if role == "dead_end_candidate" or soft_headline or body_dead_end:
        add(
            "inventory_value",
            "under_construction_or_dead_end_candidate",
            "blocker" if has_ad_code else "high",
            "This page looks like an under-construction, error, thank-you, success, or other dead-end screen"
            + (" and Google ad code/ad units were observed." if has_ad_code else "."),
            "Keep Google-served ads off under-construction/dead-end screens and replace unfinished public pages with complete publisher content before monetization.",
        )

    if quality.get("absence_supported"):
        if words < 60 and has_ad_code and not interactive:
            add(
                "inventory_value",
                "low_publisher_content_with_ads",
                "blocker",
                f"Only {words} main-content words were captured while Google ad code/ad units were observed.",
                "Do not monetize this page until it provides substantial user value. Add real publisher content or functionality; do not add filler text merely to increase word count.",
            )
        elif words < 90 and role in {"content", "commercial"} and not interactive:
            add(
                "inventory_value",
                "low_value_content_candidate",
                "high",
                f"Only {words} main-content words were captured on a {role} page.",
                "Review whether this page provides a complete, useful reason to exist before AdSense submission. Expand substance only where users genuinely need it.",
                False,
            )
        elif words < 60 and interactive:
            add(
                "inventory_value",
                "interactive_page_value_needs_review",
                "review",
                f"The page has only {words} captured words but appears interactive/tool-like.",
                "Manually verify the tool/functionality itself provides meaningful publisher value. SiteMantle does not treat low word count alone as low value on functional pages.",
                False,
            )

    internal_links = [
        l for l in links if urlsplit(l.get("url", "")).netloc == urlsplit(url).netloc
    ]
    if has_ad_code and words < 50 and len(internal_links) >= 10:
        add(
            "inventory_value",
            "navigation_screen_with_ads_candidate",
            "high",
            f"The page contains {len(internal_links)} internal links but only {words} main-content words while ad code is present.",
            "Review whether this page is primarily navigation rather than publisher content. Google-served ads should be associated with meaningful publisher content, not navigation-only screens.",
        )

    ad_units = int(dom.get("ad_units_observed") or 0)
    if ad_units >= 3 and words < 180:
        add(
            "ad_density",
            "ads_may_outweigh_publisher_content",
            "high",
            f"At least {ad_units} static Google ad-unit markers were observed with about {words} main-content words.",
            "Perform a rendered visual check and reduce ad/promotional density if ads compete with the publisher content. Dynamic ad size means this static count is only a risk signal.",
        )
    elif ad_units >= 4:
        add(
            "ad_density",
            "high_ad_unit_count_needs_visual_review",
            "medium",
            f"At least {ad_units} static Google ad-unit markers were observed.",
            "Verify the rendered page still contains more publisher content than ads/paid promotion and remains easy to use at mobile and desktop sizes.",
        )

    if dom.get("cta_near_ad_candidates"):
        labels = ", ".join(dom["cta_near_ad_candidates"][:6])
        add(
            "ad_placement",
            "accidental_click_proximity_candidate",
            "high",
            "Interactive controls with labels such as " + labels + " share close DOM context with an ad unit.",
            "Visually separate ads from download, play, navigation, conversion and other interactive controls so ads cannot be mistaken for site actions.",
        )

    if dom.get("fixed_or_overlay_ad_candidates"):
        add(
            "ad_placement",
            "fixed_or_overlay_ad_candidate",
            "medium",
            f"{dom['fixed_or_overlay_ad_candidates']} ad container(s) appear inside fixed/sticky/overlay-like DOM context.",
            "Manually verify these ads never cover content, push content off-screen, trap navigation, or create accidental clicks on supported viewport sizes.",
        )

    if dom.get("window_open_calls", 0) > 3 and has_ad_code:
        add(
            "ad_behavior",
            "multiple_popup_behavior_candidate",
            "high",
            f"The captured source contains {dom['window_open_calls']} window.open() calls while Google ad code is present.",
            "Review popup behavior. AdSense policies restrict disruptive popup/pop-under implementations and interference with normal navigation.",
        )
    elif dom.get("window_open_calls", 0):
        add(
            "ad_behavior",
            "popup_behavior_needs_review",
            "review",
            f"The captured source contains {dom['window_open_calls']} window.open() call(s).",
            "Confirm popup behavior is user-initiated, non-disruptive and compatible with current AdSense placement policies.",
        )

    if dom.get("external_embeds", 0) and words < 150:
        add(
            "originality",
            "embedded_content_value_needs_review",
            "high",
            f"{dom['external_embeds']} external iframe embed(s) were observed with only about {words} publisher-content words.",
            "Ensure embedded/copy-derived material is accompanied by substantial original commentary, curation, functionality or other publisher value.",
        )

    if ADSENSE_SENSITIVE_RE.search(text[:10000]):
        add(
            "content_policy",
            "potential_restricted_or_prohibited_topic",
            "review",
            "A small conservative keyword screen found a term associated with restricted/prohibited-content categories.",
            "Manually review the complete page against the current Google Publisher Policies/Restrictions. A keyword match alone is not a policy violation.",
        )

    return {
        "url": url,
        "role": role,
        "representation": representation,
        "publisher_content_words": words,
        "ad_signals": dom,
        "issues": issues,
    }


def _adsense_duplicate_issues(pages: list[dict], analyzed: list[dict]) -> list[dict]:
    """Find exact/near duplicate publisher content within the inspected site sample."""
    usable = []
    by_url = {p["url"]: p for p in analyzed}
    for page in pages:
        data, _ = selected_data(page)
        url = page.get("final_url") or page.get("url", "")
        text = re.sub(r"\s+", " ", data.get("main_text") or data.get("text") or "").strip()
        if len(text.split()) >= 120 and url in by_url:
            usable.append((url, text))
    pairs = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            left_url, left = usable[i]
            right_url, right = usable[j]
            if left == right:
                similarity = 1.0
            else:
                similarity = _adsense_similarity(left, right)
            if similarity >= 0.90:
                pairs.append((left_url, right_url, similarity))
                severity = "high" if similarity >= 0.97 else "medium"
                for url, other in ((left_url, right_url), (right_url, left_url)):
                    by_url[url]["issues"].append(
                        {
                            "area": "originality",
                            "code": "replicated_or_near_duplicate_content_candidate",
                            "severity": severity,
                            "evidence": f"The captured main content is {similarity:.0%} similar to {other} within this audited site sample.",
                            "action": "Review whether both URLs provide distinct publisher value. Consolidate or substantially differentiate near-duplicate/template pages instead of making small automated rewrites.",
                            "policy_backed": True,
                        }
                    )
    return [
        {"left": a, "right": b, "similarity": round(score, 4)} for a, b, score in pairs
    ]


def _adsense_privacy_status(pages: list[dict], analyzed: list[dict]) -> tuple[dict, list[dict]]:
    links = []
    privacy_pages = []
    for page in pages:
        data, _ = selected_data(page)
        url = page.get("final_url") or page.get("url", "")
        if _adsense_role(url, data) == "privacy":
            privacy_pages.append((url, data.get("main_text") or data.get("text") or ""))
        for link in data.get("links", []):
            target = link.get("url", "")
            anchor = link.get("anchor", "")
            if re.search(r"privacy|cookie policy", target + " " + anchor, re.I):
                links.append(target)
    links = sorted({u for u in links if u})
    issues = []
    if privacy_pages:
        evaluated = []
        for url, text in privacy_pages:
            lower = text.lower()
            checks = {
                "cookies": bool(re.search(r"\bcookies?\b", lower)),
                "google": "google" in lower,
                "third_party": bool(re.search(r"third[- ]part(?:y|ies)", lower)),
                "advertising": bool(re.search(r"\b(advertis(?:ing|ements?)|ads?)\b", lower)),
                "personalized_ads_or_optout": bool(
                    re.search(r"personal(?:ized|ised) (?:advertising|ads)|ads settings|opt[- ]?out", lower)
                ),
            }
            evaluated.append((sum(checks.values()), url, checks))
        _, best_url, best_checks = max(evaluated, key=lambda x: x[0])
        missing = [k for k, ok in best_checks.items() if not ok]
        if missing:
            issues.append(
                {
                    "area": "privacy",
                    "code": "privacy_disclosure_incomplete_candidate",
                    "severity": "high",
                    "evidence": "The strongest inspected privacy page still did not clearly contain all checked Google advertising disclosure concepts: "
                    + ", ".join(sorted(missing))
                    + ".",
                    "action": "Review the privacy policy against Google's current required content, including Google and third-party advertising cookies/identifiers, personalized advertising, and user opt-out information where applicable.",
                    "policy_backed": True,
                }
            )
        return {
            "status": "inspected",
            "privacy_pages": [u for u, _ in privacy_pages],
            "best_page": best_url,
            "best_page_checks": best_checks,
            "links_observed": links,
        }, issues
    if links:
        issues.append(
            {
                "area": "privacy",
                "code": "privacy_policy_linked_not_inspected",
                "severity": "review",
                "evidence": "A privacy-policy link was observed, but that page was not part of the inspected crawl sample.",
                "action": "Include the privacy page in the audit and verify its Google advertising/cookie disclosures before AdSense submission.",
                "policy_backed": True,
            }
        )
        return {"status": "linked_not_inspected", "privacy_pages": [], "links_observed": links}, issues
    issues.append(
        {
            "area": "privacy",
            "code": "privacy_policy_not_observed",
            "severity": "blocker",
            "evidence": "No inspected privacy page or privacy-policy link was observed in this crawl sample.",
            "action": "Add an accessible privacy policy before monetization and include the disclosures required for Google advertising products, cookies/identifiers, personalized advertising/opt-out information and third-party data use as applicable.",
            "policy_backed": True,
        }
    )
    return {"status": "not_observed", "privacy_pages": [], "links_observed": []}, issues



def _adsense_sitewide_quality(pages: list[dict], analyzed: list[dict], summary: dict) -> tuple[dict, list[dict]]:
    """Site-level approval-readiness heuristics. Policy-backed is explicit per finding."""
    issues = []
    by_url = {p["url"]: p for p in analyzed}
    seed = normalize_url(summary.get("seed", ""))
    seed_item = next((p for p in pages if normalize_url(p.get("final_url") or p.get("url", "")) == seed), None)
    if seed_item is None or seed_item.get("error") or not 200 <= seed_item.get("status", 0) < 300:
        issues.append({
            "area": "access",
            "code": "site_seed_not_reliably_reachable",
            "severity": "blocker",
            "evidence": "The submitted/seed URL was not captured as a usable successful page.",
            "action": "Make the submitted site publicly reachable to Google's review systems and resolve DNS, HTTP, robots, authentication or rendering failures before submission.",
            "policy_backed": True,
        })

    roles = Counter(p.get("role") for p in analyzed)
    trust_urls = [p["url"] for p in analyzed if p.get("role") == "trust"]
    has_about = any("about" in urlsplit(u).path.lower() for u in trust_urls)
    has_contact = any("contact" in urlsplit(u).path.lower() for u in trust_urls)
    if not has_about or not has_contact:
        missing = [name for name, ok in (("About", has_about), ("Contact", has_contact)) if not ok]
        issues.append({
            "area": "publisher_trust",
            "code": "publisher_transparency_pages_not_observed",
            "severity": "medium",
            "evidence": "The inspected sample did not include/link clear " + " and ".join(missing) + " publisher-transparency page(s).",
            "action": "For stronger review readiness, provide clear publisher identity/contact information and make it easy to reach through site navigation. This is a SiteMantle trust heuristic, not a claim that Google universally requires exact About/Contact URLs.",
            "policy_backed": False,
        })

    seed_links = []
    if seed_item:
        data, _ = selected_data(seed_item)
        seed_host = urlsplit(seed or seed_item.get("url", "")).netloc
        seed_links = sorted({
            normalize_url(l.get("url")) for l in data.get("links", [])
            if normalize_url(l.get("url")) and urlsplit(normalize_url(l.get("url"))).netloc == seed_host
        })
    usable_pages = [p for p in analyzed if p.get("publisher_content_words", 0) > 0]
    if len(usable_pages) >= 4 and len(seed_links) < 3:
        issues.append({
            "area": "navigation",
            "code": "weak_site_navigation_candidate",
            "severity": "medium",
            "evidence": f"At least {len(usable_pages)} usable pages were inspected, but only {len(seed_links)} internal destination(s) were observed from the seed page.",
            "action": "Make important content easy to discover through clear, easy-to-use navigation. Do not create navigation only for crawlers; preserve a sensible human information architecture.",
            "policy_backed": False,
        })

    cmp_seen = any(p.get("ad_signals", {}).get("cmp_signals_observed") for p in analyzed)
    return {
        "roles": dict(roles),
        "about_observed": has_about,
        "contact_observed": has_contact,
        "seed_internal_destinations": len(seed_links),
        "cmp_signal_observed": cmp_seen,
    }, issues


ADSENSE_BUILD_STANDARD = [
    "Monetize complete public content/tool/help pages, not private upload-processing, preview, result or download states.",
    "Every public tool page should provide genuine functionality plus original, useful explanation, limitations, supported inputs/outputs and help content where relevant; never add filler for a word-count target.",
    "Keep ads visually separate from Download, Convert, Play, Next/Previous, navigation, form and other action controls; never style or label an ad as a site control.",
    "Do not place Google-served ads on low/no publisher-content, under-construction, alert/navigation-only or dead-end screens.",
    "Do not publish copied, scraped, lightly rewritten or automatically generated pages without meaningful manual review, curation or original added value.",
    "Keep ads and paid promotion from outweighing publisher content.",
    "Provide clear site navigation and publisher transparency; keep an accessible privacy policy with Google/third-party advertising cookie and opt-out disclosures.",
    "If serving personalized ads to EEA, UK or Switzerland users, implement a Google-certified CMP/TCF workflow that matches current Google requirements.",
    "Use a Google-supported publisher language as the primary language of monetized pages.",
    "For third-party media downloaders or other copyright-sensitive tools, require a documented lawful rights/provider model before treating the feature as AdSense-ready.",
    "Do not treat SiteMantle's readiness score as Google's approval probability; Google performs the actual site/account review.",
]

def _adsense_readiness(issues: list[dict], coverage_limited: bool = True) -> dict:
    # Score unique risk codes, not repeated page instances. This is a transparent checklist,
    # never Google's private site-approval score or a statistical approval probability.
    rank = {"blocker": 4, "high": 3, "medium": 2, "low": 1, "review": 0}
    weights = {"blocker": 25, "high": 12, "medium": 6, "low": 2, "review": 0}
    strongest = {}
    for issue in issues:
        code = issue["code"]
        severity = issue["severity"]
        if code not in strongest or rank[severity] > rank[strongest[code]]:
            strongest[code] = severity
    score = max(0, 100 - sum(weights[s] for s in strongest.values()))
    counts = Counter(i["severity"] for i in issues)
    if counts["blocker"]:
        classification = "HIGH RISK"
    elif score >= 92 and not counts["high"]:
        classification = "READY FOR MANUAL REVIEW"
    elif score >= 80 and counts["high"] <= 1:
        classification = "MOSTLY READY"
    elif score >= 60:
        classification = "NEEDS WORK"
    else:
        classification = "HIGH RISK"

    if counts["blocker"] or score < 60:
        observable_band = "LOW OBSERVABLE READINESS"
        recommendation = "Do not submit yet; resolve blockers/high-risk findings and re-audit."
    elif counts["high"] or score < 85:
        observable_band = "MODERATE OBSERVABLE READINESS"
        recommendation = "Improve the reported high/medium risks before submission."
    elif coverage_limited:
        observable_band = "GOOD OBSERVABLE READINESS — LIMITED COVERAGE"
        recommendation = "The sampled evidence looks strong, but expand coverage and perform manual policy review before submission."
    else:
        observable_band = "HIGH OBSERVABLE READINESS"
        recommendation = "No major issue was observed by SiteMantle; proceed to manual review/submission knowing Google makes the final decision."
    return {
        "classification": classification,
        "checklist_score": score,
        "observable_readiness_band": observable_band,
        "submission_recommendation": recommendation,
        "approval_probability": "not_estimated",
        "score_basis": "Transparent SiteMantle checklist deductions by unique observed risk code. This is not Google's approval score or probability.",
        "unique_risk_codes": strongest,
    }


def adsense_audit(
    pages: list[dict],
    summary: dict,
    html_by_url: dict[str, str] | None = None,
) -> dict:
    html_by_url = html_by_url or {}
    analyzed = []
    for page in pages:
        url = page.get("final_url") or page.get("url", "")
        dom = _adsense_dom_signals(html_by_url.get(url, ""), url)
        analyzed.append(_adsense_page_analysis(page, dom))

    duplicate_pairs = _adsense_duplicate_issues(pages, analyzed)
    privacy, site_issues = _adsense_privacy_status(pages, analyzed)
    site_quality, quality_issues = _adsense_sitewide_quality(pages, analyzed, summary)
    site_issues.extend(quality_issues)
    page_issues = [i | {"url": p["url"]} for p in analyzed for i in p["issues"]]
    all_issues = site_issues + page_issues
    issue_summary = {
        "total": len(all_issues),
        "by_severity": dict(Counter(i["severity"] for i in all_issues)),
        "by_area": dict(Counter(i["area"] for i in all_issues)),
    }
    ads_seen = any(p["ad_signals"].get("google_ad_code_observed") for p in analyzed)
    return {
        "schema_version": 2,
        "product": "SiteMantle",
        "module": "AdSense Guard Advanced",
        "checked_at": summary.get("exported_at") or utcnow(),
        "seed": summary.get("seed"),
        "coverage_limited": summary.get("coverage_limited", True),
        "pages_analyzed": len(analyzed),
        "google_ad_code_observed": ads_seen,
        "readiness": _adsense_readiness(all_issues, summary.get("coverage_limited", True)),
        "issue_summary": issue_summary,
        "sitewide": {
            "privacy": privacy,
            "quality_and_navigation": site_quality,
            "near_duplicate_pairs": duplicate_pairs,
            "regional_consent": {
                "status": "not_assessed",
                "note": "If personalized ads are served to users in the EEA, UK or Switzerland, current Google requirements include a certified CMP/TCF workflow. Traffic geography and consent behavior are not established by a crawl alone.",
            },
            "ads_txt": {
                "status": "not_checked",
                "note": "ads.txt is highly recommended by Google but is not mandatory. This offline report does not fetch /ads.txt separately.",
            },
        },
        "pages": analyzed,
        "site_issues": site_issues,
        "build_standard": list(ADSENSE_BUILD_STANDARD),
        "official_policy_basis": list(ADSENSE_POLICY_REFERENCES),
        "not_measured": [
            "Google AdSense application/review outcome or approval probability",
            "whether content was written by a human or generated by AI",
            "whole-web plagiarism or originality outside the inspected site sample",
            "actual rendered ad sizes/dynamic placements unless separately visually inspected",
            "legal compliance or whether a consent notice satisfies local law",
            "traffic quality, invalid traffic, account history or private AdSense signals",
            "the applicant's age, account eligibility, tax/payment status or domain-ownership verification",
            "whether a third-party media/download feature has lawful licensing or provider authorization",
        ],
        "interpretation": "SiteMantle AdSense Guard Advanced checks observable website evidence against selected current Google publisher/AdSense requirements plus clearly-labeled approval-readiness heuristics. It cannot guarantee, predict, or certify Google approval, and its observable-readiness band is not an approval probability.",
    }



def adsense_build_standard() -> dict:
    """Return SiteMantle's AdSense-first requirements before a website is built."""
    return {
        "schema_version": 1,
        "product": "SiteMantle",
        "module": "AdSense Guard Advanced — Pre-Build Standard",
        "rules": list(ADSENSE_BUILD_STANDARD),
        "submission_checks": [
            {"area": "content", "requirement": "Original, useful publisher content with real added value; no copied/lightly rewritten or unreviewed auto-generated inventory.", "basis": "Google policy/readiness"},
            {"area": "ux_navigation", "requirement": "Clear, easy-to-use navigation and a complete production-quality site; no construction/dead-end monetized screens.", "basis": "Google readiness/policy"},
            {"area": "tool_states", "requirement": "Public tool/help pages may be monetized only when valuable and safe; private processing, preview, result and download states stay ad-free.", "basis": "Google inventory-value/interactions + SiteMantle architecture rule"},
            {"area": "ad_placement", "requirement": "Ads must not resemble or sit dangerously close to Download, Convert, Play, navigation, forms or other actions.", "basis": "Google policy"},
            {"area": "privacy", "requirement": "Accessible privacy policy covering Google/third-party advertising cookies and personalized-ad/opt-out information as applicable.", "basis": "Google required content"},
            {"area": "consent", "requirement": "Use a Google-certified CMP/TCF workflow when serving personalized ads to users in the EEA, UK or Switzerland.", "basis": "Google regional consent requirement"},
            {"area": "language", "requirement": "Monetized pages use a currently supported Google publisher language as their primary language.", "basis": "Google policy"},
            {"area": "rights", "requirement": "Copyright/provider-sensitive download features require a documented lawful rights/provider model before being treated as AdSense-ready.", "basis": "Google IP policy + SiteMantle risk rule"},
            {"area": "submission", "requirement": "Site ownership/control, applicant/account eligibility, ad-code setup and traffic quality must be checked separately because a public crawl cannot prove them.", "basis": "Google eligibility/site review + unmeasured private signals"},
        ],
        "approval_probability": "not_estimated",
        "interpretation": "Use this before coding an AdSense-first website. It is a build standard and policy-readiness checklist, not a guarantee or probability of Google approval.",
        "official_policy_basis": list(ADSENSE_POLICY_REFERENCES),
    }


def export_adsense_build_standard(out: Path | str):
    out = Path(out)
    result = adsense_build_standard()
    lines = [
        "# SiteMantle AdSense Guard Advanced — Pre-Build Standard",
        "",
        result["interpretation"],
        "",
        "## Build rules",
        "",
        *[f"{i}. {rule}" for i, rule in enumerate(result["rules"], 1)],
        "",
        "## Submission checks",
        "",
    ]
    for item in result["submission_checks"]:
        lines += [f"### {item['area']}", "", item["requirement"], "", f"Basis: {item['basis']}", ""]
    lines += [
        "## Important note",
        "",
        "SiteMantle does not estimate Google's private approval probability. A site can satisfy all observable checks and still require Google review/account-level checks.",
    ]
    return save_report(out, "adsense-build-standard", result, lines)


def _captured_html_for_adsense(out: Path, page: dict) -> str:
    _, representation = selected_data(page)
    rel = None
    if representation == "rendered":
        rel = (page.get("rendered") or {}).get("html_path")
    if not rel:
        rel = page.get("html_path")
    if not rel:
        return ""
    root = out.resolve()
    candidate = (out / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return ""
    if not candidate.is_file() or candidate.stat().st_size > 8_000_000:
        return ""
    return candidate.read_text(encoding="utf-8", errors="replace")


def export_adsense_audit(out: Path | str):
    out = Path(out)
    pages = read_pages(out)
    summary = read_json(out / "summary.json")
    html_by_url = {
        (p.get("final_url") or p.get("url", "")): _captured_html_for_adsense(out, p)
        for p in pages
    }
    result = adsense_audit(pages, summary, html_by_url)
    r = result["readiness"]
    lines = [
        "# SiteMantle AdSense Guard Advanced",
        "",
        result["interpretation"],
        "",
        f"**Readiness:** {r['classification']}",
        f"**Checklist score:** {r['checklist_score']}/100 (not an approval probability)",
        f"**Observable readiness:** {r['observable_readiness_band']}",
        f"**Submission guidance:** {r['submission_recommendation']}",
        f"**Pages analyzed:** {result['pages_analyzed']}",
        "",
        "## Priority findings",
        "",
    ]
    priority = list(result["site_issues"]) + [
        i | {"url": p["url"]} for p in result["pages"] for i in p["issues"]
    ]
    order = {"blocker": 0, "high": 1, "medium": 2, "low": 3, "review": 4}
    priority.sort(key=lambda x: (order.get(x["severity"], 9), x.get("code", "")))
    if not priority:
        lines.append(
            "No issue was flagged by these bounded checks. Google still performs its own review, and this does not guarantee approval."
        )
    for item in priority:
        location = item.get("url", "Site-wide")
        lines += [
            f"### {item['severity'].upper()} · {item['area']} · {location}",
            "",
            item["evidence"],
            "",
            "**Recommended action:** " + item["action"],
            "",
        ]
    lines += [
        "## AdSense-first build standard",
        "",
        *["- " + x for x in result["build_standard"]],
        "",
        "## Important limits",
        "",
        *["- " + x for x in result["not_measured"]],
        "",
        "## Policy basis",
        "",
        *[f"- [{p['name']}]({p['url']}) — {p['covers']}" for p in result["official_policy_basis"]],
    ]
    return save_report(out, "adsense-readiness", result, lines)
