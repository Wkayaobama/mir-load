from __future__ import annotations

from pipeline.library_files.card import load_library_card


def test_card_scope_and_rules():
    card = load_library_card()
    assert card.roots[0].name == "20 opportunities and customer data"
    assert card.roots[0].path_prefix == "30 Sales"
    assert card.is_excluded_segment("70 Tradeshows")
    assert card.is_excluded_segment("Events & Tradeshows")
    assert not card.is_excluded_segment("20 Opportunities and customer data")
    assert card.is_excluded_path("Quantum/70 Tradeshows/booth.pdf")


def test_asset_classification_first_match_wins():
    card = load_library_card()
    assert card.classify_asset(name="PO_2026_001.pdf", mime="application/pdf") == "deal_candidate"
    assert card.classify_asset(name="Billing Q3.PDF", mime="application/pdf") == "deal_candidate"
    assert card.classify_asset(name="datasheet.pdf", mime="application/pdf") == "parked_for_review"
    assert card.classify_asset(name="PO_notes.docx", mime="application/x") == "asset"   # PO but not pdf
    assert card.classify_asset(name="x", mime="application/vnd.google-apps.shortcut") == "shortcut"


def test_deal_inference_card_and_dbt_var_agree():
    """The heuristic lives in the card (Python side) and in dbt vars (SQL side); they must match."""
    import yaml
    from pathlib import Path
    from pipeline.library_files.card import load_library_card
    card = load_library_card()
    proj = yaml.safe_load((Path(__file__).resolve().parents[1] / "dbt" / "dbt_project.yml").read_text())
    assert proj["vars"]["deal_exclude_regex"] == card.deal_exclude_regex()
    assert int(proj["vars"]["deal_min_pdf"]) == card.deal_min_pdf
    assert card.is_deal_excluded_name("2023 - Enlit Exibition (Paris)") and not card.is_deal_excluded_name("2026 Quantum sensor RFQ")
