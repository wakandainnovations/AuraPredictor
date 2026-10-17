from __future__ import annotations

from aurapredictor.model.dataset import (
    fit_production_company_binarizer,
    has_plausible_revenue,
    normalize_release_event_name,
    split_production_companies,
)


def test_has_plausible_revenue_drops_scrape_errors():
    assert has_plausible_revenue({"revenue": 3_000_000, "budget": 2_000_000})
    assert not has_plausible_revenue({"revenue": 69, "budget": 11_000_000})  # below the absolute floor
    assert not has_plausible_revenue({"revenue": 43_158, "budget": 50_000_000})  # < 1% of budget


def test_normalize_release_event_name_strips_years():
    assert normalize_release_event_name("2011 ICC Cricket World Cup") == "ICC Cricket World Cup"
    assert normalize_release_event_name("Diwali") == "Diwali"
    assert normalize_release_event_name(None) == "Unknown"


def test_production_company_binarizer_keeps_every_credited_company_above_threshold():
    rows = [{"production_companies": "Big Studio, Small Co"}] * 5 + [{"production_companies": "Small Co, Rare Co"}]
    binarizer = fit_production_company_binarizer(rows)
    assert list(binarizer.classes_) == ["Big Studio", "Small Co"]
    assert split_production_companies("B, A, B") == ["A", "B"]
