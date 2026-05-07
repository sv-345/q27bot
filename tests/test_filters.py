from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from filters import FilterRules, title_passes


@pytest.fixture(scope="module")
def rules() -> FilterRules:
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "firms.yaml").read_text())
    return FilterRules.from_dict(cfg["profiles"]["quant"]["filters"])


@pytest.fixture(scope="module")
def tech_rules() -> FilterRules:
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "firms.yaml").read_text())
    return FilterRules.from_dict(cfg["profiles"]["tech"]["filters"])


@pytest.mark.parametrize(
    "title",
    [
        "Quantitative Trader Intern - Summer 2027",
        "Quant Research Intern",
        "Quantitative Researcher Intern, Summer 2027",
        "Algorithmic Trading Intern",
        "Systematic Research Internship",
        "Trading Intern - 2027",
        # Year present alongside reject year — 2027 still wins.
        "Quant Trading Intern (2026/2027 Cycle)",
    ],
)
def test_pass(rules: FilterRules, title: str) -> None:
    assert title_passes(title, rules), title


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern",
        "SWE Intern - Trading Tech",  # excluded substring wins
        "Marketing Intern - Summer 2027",
        "Quant Trader Intern - Summer 2025",  # reject year, no 2027
        "Quantitative Research Intern - Summer 2026",
        "HR Intern",
        "Data Engineer Intern - Quant Team",  # excluded
        "Compliance Intern",
        "Quant Researcher",  # no "intern" word
        "Senior Quant Trader",  # no intern
        "Graphic Design Intern",
        "",
    ],
)
def test_reject(rules: FilterRules, title: str) -> None:
    assert not title_passes(title, rules), title


def test_no_year_passes(rules: FilterRules) -> None:
    """Spec: titles with no year mentioned at all should pass."""
    assert title_passes("Quant Trader Intern", rules)
    assert title_passes("Quantitative Research Intern", rules)


# ---------- tech profile (SWE / data science / finance / S&T) ----------


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer Intern - Summer 2027",
        "SWE Intern",
        "Data Scientist Intern",
        "Data Science Intern",
        "Machine Learning Engineer Intern",
        "ML Intern - Summer 2027",
        "AI Research Intern",
        "Backend Engineer Intern",
        "Frontend Developer Intern",
        "Cloud Infrastructure Intern",
        "Sales & Trading Summer Analyst",
        "Sales and Trading Intern - Summer 2027",
        "Investment Banking Summer Analyst",
        "Finance Summer Analyst",
        "Markets Summer Analyst",
        "Capital Markets Intern",
    ],
)
def test_tech_pass(tech_rules: FilterRules, title: str) -> None:
    assert title_passes(title, tech_rules), title


@pytest.mark.parametrize(
    "title",
    [
        "HR Intern",
        "Marketing Intern - Summer 2027",
        "Compliance Intern",
        "Legal Summer Intern",
        "Audit Intern",
        "Graphic Design Intern",
        "Software Engineer Intern - Summer 2025",  # reject year, no 2027
        "Senior Software Engineer",  # no intern
    ],
)
def test_tech_reject(tech_rules: FilterRules, title: str) -> None:
    assert not title_passes(title, tech_rules), title
