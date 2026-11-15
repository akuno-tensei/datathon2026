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


def test_periods_read_as_plain_language_and_dates_are_readable():
    from alphaforge.app.labels import human_date, window_name

    assert humanize_text("Volatilité de NVDA : 61,3 % (fenêtre « 2022 »)") == "Volatilité de NVDA : 61,3 % (en 2022)"
    assert humanize_text("NVDA, fenêtre « complet » (du 2021-11-12 au 2026-11-12)") == "NVDA, sur toute la période (du 12 nov. 2021 au 12 nov. 2026)"
    assert humanize_text("va de 1 % à 2 % selon la fenêtre (complet, 2022)") == "va de 1 % à 2 % selon la période (toute la période, année 2022)"
    assert humanize_text("Ce repli n'était pas récupéré à la fin de la fenêtre.") == "Le prix n'était pas encore revenu à son sommet à la fin de la période."
    assert window_name("hors_2022") == "toute la période sauf 2022" and window_name("2023-01-01/2026-11-12") == "du 1 janv. 2023 au 12 nov. 2026"
    assert human_date("2026-11-12", "en") == "Nov 12, 2026" and human_date("pas une date") == "pas une date"
    assert humanize_text("SPY (window \"1_an\")", "en") == "SPY (over the last year)"


def test_numbers_are_never_changed_by_the_rewording():
    import re

    s = "Volatilité annualisée de TLT : 20,7 % (fenêtre « 2022 »), pour une limite de 15,0 %, soit 1,4 fois la limite."
    nums = lambda x: re.findall(r"\d+,\d+", x)
    assert nums(humanize_text(s)) == nums(s)


def test_tools_and_intents_are_named_in_plain_words():
    assert humanize_text("compute_metrics  (NVDA)") == "Mesures de risque et de rendement  (NVDA)"
    assert humanize_text("intention « compare_assets »") == "intention « Comparer des actifs »"
