"""Libellés d'affichage : lisibles, sans jamais modifier un mot inconnu."""
from alphaforge.app.labels import human, humanize_text


def test_known_codes_get_a_readable_label_in_both_languages():
    assert human("faible_modere") == "Faible à modéré" and human("faible_modere", "en") == "Low to moderate"
    assert human("preservation_capital") == "Préservation du capital" and human("etf_bond") == "FNB obligataire"
    assert human("no_provider") == "aucun fournisseur branché"


def test_unknown_values_lose_underscores_and_named_values_are_untouched():
    assert human("intelligence_artificielle") == "Intelligence artificielle"
    assert human("NVDA") == "NVDA" and human("RY.TO") == "RY.TO" and human("Information Technology") == "Information Technology"


def test_sentences_only_change_known_codes():
    assert humanize_text("niveau de risque « faible_modere » appliqué") == "niveau de risque « Faible à modéré » appliqué"
    assert humanize_text("préférence eviter_controverses non vérifiée") == "préférence éviter les controverses non vérifiée"
    assert humanize_text("run_tool(store, retraite_70_ans) ev_ab12") == "run_tool(store, retraite_70_ans) ev_ab12"
    assert humanize_text("thème esg") == "thème esg"  # mot simple hors guillemets : laissé tel quel
