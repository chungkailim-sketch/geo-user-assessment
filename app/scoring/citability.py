"""AI citability scoring.

`score_passage` and `extract_blocks` are ported from the GEO skill's
`scripts/citability_scorer.py` (scoring logic kept verbatim). Site-level
aggregation follows `references/modules/citability.md`.
"""

import re
from typing import Optional

from bs4 import BeautifulSoup

SUBSCORE_MAX = {
    "answer_block_quality": 30,
    "self_containment": 25,
    "structural_readability": 20,
    "statistical_density": 15,
    "uniqueness_signals": 10,
}
SUBSCORE_LABELS = {
    "answer_block_quality": "Answer Block Quality",
    "self_containment": "Self-Containment",
    "structural_readability": "Structural Readability",
    "statistical_density": "Statistical Density",
    "uniqueness_signals": "Uniqueness & Original Data",
}
REWRITE_TIPS = {
    "answer_block_quality": "Open with a direct answer in the first sentence (e.g. \"X is ...\") and use a question-style heading.",
    "self_containment": "Make the passage stand alone: aim for 134-167 words, name the brand/product instead of 'it/this/they'.",
    "structural_readability": "Break into short 10-20 word sentences and use numbered steps or lists.",
    "statistical_density": "Add specific numbers, percentages, years and a named source (\"according to ...\").",
    "uniqueness_signals": "Add original data, a concrete example or case study (\"our research found ...\").",
}


def score_passage(text: str, heading: Optional[str] = None) -> dict:
    """Score a single passage for AI citability (0-100). Ported verbatim from the GEO skill."""
    words = text.split()
    word_count = len(words)

    scores = {
        "answer_block_quality": 0,
        "self_containment": 0,
        "structural_readability": 0,
        "statistical_density": 0,
        "uniqueness_signals": 0,
    }

    # === 1. Answer Block Quality (30%) ===
    abq_score = 0
    definition_patterns = [
        r"\b\w+\s+is\s+(?:a|an|the)\s",
        r"\b\w+\s+refers?\s+to\s",
        r"\b\w+\s+means?\s",
        r"\b\w+\s+(?:can be |are )?defined\s+as\s",
        r"\bin\s+(?:simple|other)\s+(?:terms|words)\s*,",
    ]
    for pattern in definition_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            abq_score += 15
            break

    first_60_words = " ".join(words[:60])
    if any(
        re.search(p, first_60_words, re.IGNORECASE)
        for p in [
            r"\b(?:is|are|was|were|means?|refers?)\b",
            r"\d+%",
            r"\$[\d,]+",
            r"\d+\s+(?:million|billion|thousand)",
        ]
    ):
        abq_score += 15

    if heading and heading.endswith("?"):
        abq_score += 10

    sentences = re.split(r"[.!?]+", text)
    short_clear_sentences = sum(1 for s in sentences if 5 <= len(s.split()) <= 25)
    if sentences:
        clarity_ratio = short_clear_sentences / len(sentences)
        abq_score += int(clarity_ratio * 10)

    if re.search(
        r"(?:according to|research shows|studies? (?:show|indicate|suggest|found)|data (?:shows|indicates|suggests))",
        text,
        re.IGNORECASE,
    ):
        abq_score += 10

    scores["answer_block_quality"] = min(abq_score, 30)

    # === 2. Self-Containment (25%) ===
    sc_score = 0
    if 134 <= word_count <= 167:
        sc_score += 10
    elif 100 <= word_count <= 200:
        sc_score += 7
    elif 80 <= word_count <= 250:
        sc_score += 4
    elif word_count < 30 or word_count > 400:
        sc_score += 0
    else:
        sc_score += 2

    pronoun_count = len(
        re.findall(
            r"\b(?:it|they|them|their|this|that|these|those|he|she|his|her)\b",
            text,
            re.IGNORECASE,
        )
    )
    if word_count > 0:
        pronoun_ratio = pronoun_count / word_count
        if pronoun_ratio < 0.02:
            sc_score += 8
        elif pronoun_ratio < 0.04:
            sc_score += 5
        elif pronoun_ratio < 0.06:
            sc_score += 3

    proper_nouns = len(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text))
    if proper_nouns >= 3:
        sc_score += 7
    elif proper_nouns >= 1:
        sc_score += 4

    scores["self_containment"] = min(sc_score, 25)

    # === 3. Structural Readability (20%) ===
    sr_score = 0
    if sentences:
        avg_sentence_length = word_count / len(sentences)
        if 10 <= avg_sentence_length <= 20:
            sr_score += 8
        elif 8 <= avg_sentence_length <= 25:
            sr_score += 5
        else:
            sr_score += 2

    if re.search(r"(?:first|second|third|finally|additionally|moreover|furthermore)", text, re.IGNORECASE):
        sr_score += 4
    if re.search(r"(?:\d+[\.\)]\s|\b(?:step|tip|point)\s+\d+)", text, re.IGNORECASE):
        sr_score += 4
    if "\n" in text:
        sr_score += 4

    scores["structural_readability"] = min(sr_score, 20)

    # === 4. Statistical Density (15%) ===
    sd_score = 0
    pct_count = len(re.findall(r"\d+(?:\.\d+)?%", text))
    sd_score += min(pct_count * 3, 6)

    dollar_count = len(re.findall(r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B|K))?", text))
    sd_score += min(dollar_count * 3, 5)

    number_count = len(
        re.findall(
            r"\b\d+(?:,\d{3})*(?:\.\d+)?\s+(?:users|customers|pages|sites|companies|businesses|people|percent|times|x\b)",
            text,
            re.IGNORECASE,
        )
    )
    sd_score += min(number_count * 2, 4)

    year_count = len(re.findall(r"\b20(?:2[3-6]|1\d)\b", text))
    if year_count > 0:
        sd_score += 2

    source_patterns = [
        r"(?:according to|per|from|by)\s+[A-Z]",
        r"(?:Gartner|Forrester|McKinsey|Harvard|Stanford|MIT|Google|Microsoft|OpenAI|Anthropic)",
        r"\([A-Z][a-z]+(?:\s+\d{4})?\)",
    ]
    for pattern in source_patterns:
        if re.search(pattern, text):
            sd_score += 2

    scores["statistical_density"] = min(sd_score, 15)

    # === 5. Uniqueness Signals (10%) ===
    us_score = 0
    if re.search(
        r"(?:our (?:research|study|data|analysis|survey|findings)|we (?:found|discovered|analyzed|surveyed|measured))",
        text,
        re.IGNORECASE,
    ):
        us_score += 5
    if re.search(
        r"(?:case study|for example|for instance|in practice|real-world|hands-on)",
        text,
        re.IGNORECASE,
    ):
        us_score += 3
    if re.search(r"(?:using|with|via|through)\s+[A-Z][a-z]+", text):
        us_score += 2

    scores["uniqueness_signals"] = min(us_score, 10)

    total = sum(scores.values())
    if total >= 80:
        grade, label = "A", "Highly Citable"
    elif total >= 65:
        grade, label = "B", "Good Citability"
    elif total >= 50:
        grade, label = "C", "Moderate Citability"
    elif total >= 35:
        grade, label = "D", "Low Citability"
    else:
        grade, label = "F", "Poor Citability"

    return {
        "heading": heading,
        "word_count": word_count,
        "total_score": total,
        "grade": grade,
        "label": label,
        "breakdown": scores,
        "preview": " ".join(words[:30]) + ("..." if word_count > 30 else ""),
    }


def extract_blocks(html: str) -> list[dict]:
    """Split a page into heading-delimited content blocks (ported from the GEO skill)."""
    soup = BeautifulSoup(html, "lxml")
    for element in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        element.decompose()

    blocks: list[dict] = []
    current_heading = "Introduction"
    current_paragraphs: list[str] = []

    def flush() -> None:
        if current_paragraphs:
            combined = " ".join(current_paragraphs)
            if len(combined.split()) >= 20:
                blocks.append({"heading": current_heading, "content": combined})

    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "ul", "ol", "table"]):
        if element.name.startswith("h"):
            flush()
            current_heading = element.get_text(" ", strip=True)[:200]
            current_paragraphs = []
        else:
            text = element.get_text(" ", strip=True)
            if text and len(text.split()) >= 5:
                current_paragraphs.append(text)
    flush()
    return blocks


def weakest_dimension(breakdown: dict) -> str:
    return min(breakdown, key=lambda key: breakdown[key] / SUBSCORE_MAX[key])


def analyze_page(url: str, html: str) -> dict:
    scored = [score_passage(b["content"], b["heading"]) for b in extract_blocks(html)]
    for block in scored:
        block["url"] = url
    average = sum(b["total_score"] for b in scored) / len(scored) if scored else 0.0
    return {"url": url, "blocks": scored, "score": round(average, 1)}


def analyze_site(pages: list[tuple[str, str]]) -> dict:
    """Aggregate citability across pages. `pages` is a list of (url, html)."""
    page_results = [analyze_page(url, html) for url, html in pages]
    scored_pages = [p for p in page_results if p["blocks"]]
    all_blocks = [b for p in scored_pages for b in p["blocks"]]

    site_score = sum(p["score"] for p in scored_pages) / len(scored_pages) if scored_pages else 0.0

    subscores = {}
    for key, maximum in SUBSCORE_MAX.items():
        mean = sum(b["breakdown"][key] for b in all_blocks) / len(all_blocks) if all_blocks else 0.0
        subscores[key] = round(mean / maximum * 100, 1)

    grade_distribution = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for block in all_blocks:
        grade_distribution[block["grade"]] += 1

    total = len(all_blocks)
    coverage = sum(1 for b in all_blocks if b["total_score"] > 70) / total * 100 if total else 0.0
    optimal = sum(1 for b in all_blocks if 134 <= b["word_count"] <= 167) / total * 100 if total else 0.0

    ranked = sorted(all_blocks, key=lambda b: b["total_score"], reverse=True)
    top = [_public_block(b) for b in ranked[:3]]
    bottom = [_with_tip(_public_block(b), b) for b in ranked[-3:][::-1] if b["total_score"] < 60] if total > 3 else []

    return {
        "score": round(site_score, 1),
        "subscores": subscores,
        "subscore_labels": SUBSCORE_LABELS,
        "blocks_analyzed": total,
        "pages_analyzed": len(page_results),
        "pages_without_content": [p["url"] for p in page_results if not p["blocks"]],
        "coverage_pct": round(coverage, 1),
        "optimal_length_pct": round(optimal, 1),
        "grade_distribution": grade_distribution,
        "top_passages": top,
        "bottom_passages": bottom,
        "pages": [{"url": p["url"], "score": p["score"], "blocks": len(p["blocks"])} for p in page_results],
    }


def _public_block(block: dict) -> dict:
    return {
        "url": block["url"],
        "heading": block["heading"],
        "score": block["total_score"],
        "grade": block["grade"],
        "word_count": block["word_count"],
        "preview": block["preview"],
    }


def _with_tip(public: dict, block: dict) -> dict:
    dimension = weakest_dimension(block["breakdown"])
    return {**public, "weakest": SUBSCORE_LABELS[dimension], "tip": REWRITE_TIPS[dimension]}
