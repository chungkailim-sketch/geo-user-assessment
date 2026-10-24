import pytest

from app.scoring.citability import analyze_site, extract_blocks, score_passage, weakest_dimension

pytestmark = pytest.mark.unit

DEFINITION = (
    "Generative engine optimization is a practice that structures web content so AI assistants can quote it. "
    "According to Gartner, 25% of search volume will shift to AI chatbots by 2026. "
    "First, write a direct answer. Second, add statistics. Our research found that pages using Google "
    "structured data earn 30% more citations. For example, a 150 word answer block performs best."
)


def test_definition_passage_scores_high_on_answer_quality():
    result = score_passage(DEFINITION, "What is generative engine optimization?")
    assert result["breakdown"]["answer_block_quality"] == 30
    assert result["breakdown"]["statistical_density"] >= 8
    assert result["breakdown"]["uniqueness_signals"] >= 8
    assert result["total_score"] == sum(result["breakdown"].values())


def test_short_pronoun_heavy_passage_scores_poorly():
    result = score_passage("It is what they wanted and this is that thing they liked about it.")
    assert result["grade"] in ("D", "F")
    assert result["breakdown"]["self_containment"] < 10


def test_subscores_respect_caps():
    text = " ".join([DEFINITION] * 3)
    result = score_passage(text, "Why?")
    caps = {"answer_block_quality": 30, "self_containment": 25, "structural_readability": 20,
            "statistical_density": 15, "uniqueness_signals": 10}
    for key, cap in caps.items():
        assert 0 <= result["breakdown"][key] <= cap


def test_extract_blocks_strips_chrome_and_groups_by_heading():
    html = """<html><body><nav><p>Home About Products Contact Support Careers</p></nav>
    <h2>First section</h2><p>%s</p><h2>Too short</h2><p>Only five words here ok.</p>
    <footer><p>Copyright notice with enough words to be counted as a paragraph if not removed.</p></footer>
    </body></html>""" % DEFINITION
    blocks = extract_blocks(html)
    assert [b["heading"] for b in blocks] == ["First section"]
    assert "Copyright" not in blocks[0]["content"]


def test_extract_blocks_keeps_word_boundaries_between_inline_tags():
    html = "<h2>H</h2><p>" + "<b>Alpha</b>beta " * 12 + "</p>"
    blocks = extract_blocks(html)
    assert "Alpha beta" in blocks[0]["content"]


def test_analyze_site_aggregates_pages():
    page = f"<h2>What is GEO?</h2><p>{DEFINITION}</p>"
    result = analyze_site([("https://a.example/", page), ("https://a.example/empty", "<p>hi</p>")])
    assert result["pages_analyzed"] == 2
    assert result["blocks_analyzed"] == 1
    assert result["pages_without_content"] == ["https://a.example/empty"]
    assert result["score"] == result["pages"][0]["score"]
    assert set(result["subscores"]) == set(result["subscore_labels"])
    assert all(0 <= v <= 100 for v in result["subscores"].values())
    assert sum(result["grade_distribution"].values()) == 1


def test_analyze_site_with_no_content_returns_zero():
    result = analyze_site([("https://a.example/", "<div id='root'></div>")])
    assert result["score"] == 0
    assert result["coverage_pct"] == 0
    assert result["top_passages"] == []


def test_bottom_passages_include_rewrite_tip():
    weak = "<h2>Note</h2><p>" + "It is that thing they do with this and those things there. " * 4 + "</p>"
    strong = f"<h2>What is GEO?</h2><p>{DEFINITION}</p>"
    html = strong * 2 + weak * 3
    result = analyze_site([("https://a.example/", html)])
    assert result["bottom_passages"]
    assert all("tip" in p and "weakest" in p for p in result["bottom_passages"])


def test_weakest_dimension_is_relative_to_cap():
    breakdown = {"answer_block_quality": 15, "self_containment": 20, "structural_readability": 16,
                 "statistical_density": 3, "uniqueness_signals": 8}
    assert weakest_dimension(breakdown) == "statistical_density"
