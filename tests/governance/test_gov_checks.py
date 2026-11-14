"""Contrôles purs : profil + faits écrits à la main -> raisons. Aucun accès aux données."""
from datetime import date

import pytest

from alphaforge.governance import (
    GovernanceConfig, check_data_gaps, check_hard_constraints, check_horizon_fit, check_risk_limits,
    check_scope_limits, check_soft_preferences, derive_constraints,
)
from alphaforge.governance.checks import status_of, worst_status
from alphaforge.governance.config import RISK_LEVELS, RiskLimits
from alphaforge.governance.facts import Refusal
from alphaforge.governance.models import Reason
from alphaforge.orchestrator import load_profile

from helpers_gov import facts, window

CFG = GovernanceConfig()


def prof(profile_dict, **over):
    return load_profile(profile_dict(**over))


def codes(reasons):
    return [r.code for r in reasons]


# ---- derive_constraints ---------------------------------------------------


@pytest.mark.parametrize("level", RISK_LEVELS)
def test_limits_follow_the_config_table(profile_dict, level):
    c = derive_constraints(prof(profile_dict, risk_tolerance=level), CFG)
    lim = CFG.risk_limits[level]
    assert (c.max_volatility, c.max_drawdown, c.max_single_asset_weight) == (lim.max_volatility, lim.max_drawdown, lim.max_single_asset_weight)
    assert c.risk_level_applied == level and c.adjustments == []


def test_limits_grow_with_risk_tolerance(profile_dict):
    cs = [derive_constraints(prof(profile_dict, risk_tolerance=lv), CFG) for lv in RISK_LEVELS]
    for a, b in zip(cs, cs[1:]):
        assert a.max_volatility < b.max_volatility and a.max_drawdown < b.max_drawdown
        assert a.max_single_asset_weight <= b.max_single_asset_weight
        assert a.class_caps.get("crypto", 1.0) <= b.class_caps.get("crypto", 1.0)


def test_short_horizon_tightens_one_level_and_says_so(profile_dict):
    c = derive_constraints(prof(profile_dict, risk_tolerance="eleve", horizon_years_min=0, horizon_years_max=2), CFG)
    assert c.risk_level_applied == "modere_eleve"
    assert len(c.adjustments) == 1 and "resserrées" in c.adjustments[0]


def test_horizon_at_threshold_is_not_tightened(profile_dict):
    c = derive_constraints(prof(profile_dict, risk_tolerance="modere", horizon_years_min=1, horizon_years_max=CFG.short_horizon_years), CFG)
    assert c.risk_level_applied == "modere" and c.adjustments == []


def test_lowest_level_cannot_be_tightened(profile_dict):
    c = derive_constraints(prof(profile_dict, risk_tolerance="faible", horizon_years_min=0, horizon_years_max=1), CFG)
    assert c.risk_level_applied == "faible" and c.adjustments == []


def test_constraints_do_not_depend_on_identity(profile_dict):
    a = derive_constraints(prof(profile_dict, profile_id="robert", display_name="Robert"), CFG)
    b = derive_constraints(prof(profile_dict, profile_id="amira", display_name="Amira"), CFG)
    assert a.model_dump(exclude={"profile_id"}) == b.model_dump(exclude={"profile_id"})


def test_custom_config_is_used(profile_dict):
    cfg = GovernanceConfig(risk_limits={**CFG.risk_limits, "modere": RiskLimits(max_volatility=0.5, max_drawdown=0.5, max_single_asset_weight=0.5)})
    assert derive_constraints(prof(profile_dict), cfg).max_volatility == 0.5


# ---- check_hard_constraints ----------------------------------------------


def hard(profile_dict, f, exclusions=(), **over):
    p = prof(profile_dict, hard_exclusions=list(exclusions), **over)
    return check_hard_constraints(p, derive_constraints(p, CFG), f)


@pytest.mark.parametrize(
    "exclusion,code",
    [
        ({"kind": "sector", "value": "  tech "}, "EXCLUDED_SECTOR"),
        ({"kind": "asset_class", "value": "EQUITY"}, "EXCLUDED_ASSET_CLASS"),
        ({"kind": "ticker", "value": "x"}, "EXCLUDED_TICKER"),
    ],
)
def test_matching_exclusion_blocks(profile_dict, exclusion, code):
    r = hard(profile_dict, facts(), [exclusion])
    assert codes(r) == [code] and r[0].severity == "block"
    assert r[0].evidence_ids == ["ev_lookup"]


def test_ticker_exclusion_matches_root_of_listing_symbol(profile_dict):
    r = hard(profile_dict, facts(ticker="RY.TO"), [{"kind": "ticker", "value": "ry"}])
    assert codes(r) == ["EXCLUDED_TICKER"]


def test_exclusion_reason_is_reported(profile_dict):
    r = hard(profile_dict, facts(), [{"kind": "sector", "value": "Tech", "reason": "choix éthique"}])
    assert "choix éthique" in r[0].message


def test_non_matching_exclusion_is_silent(profile_dict):
    assert hard(profile_dict, facts(), [{"kind": "sector", "value": "Energy"}, {"kind": "ticker", "value": "ZZZ"}]) == []


def test_theme_exclusion_warns_but_never_blocks(profile_dict):
    r = hard(profile_dict, facts(), [{"kind": "theme", "value": "tabac"}])
    assert codes(r) == ["THEME_EXCLUSION_UNVERIFIED"] and r[0].severity == "warn"


def test_unknown_asset_is_not_judged(profile_dict):
    assert hard(profile_dict, facts(with_asset=False), [{"kind": "sector", "value": "Tech"}]) == []


def test_class_with_zero_cap_is_not_permitted(profile_dict):
    r = hard(profile_dict, facts(asset_class="crypto"), risk_tolerance="faible")
    assert codes(r) == ["CLASS_NOT_PERMITTED"] and r[0].severity == "block"
    assert r[0].measures["class_cap"] == 0.0 and r[0].measures["risk_level_applied"] == "faible"


def test_class_with_positive_cap_is_permitted(profile_dict):
    assert hard(profile_dict, facts(asset_class="crypto"), risk_tolerance="modere") == []


def test_short_horizon_can_make_a_class_not_permitted(profile_dict):
    # modere autorise 10 % de crypto ; un horizon d'un an resserre à faible_modere (0 %).
    assert hard(profile_dict, facts(asset_class="crypto"), risk_tolerance="modere", horizon_years_min=0, horizon_years_max=1)[0].code == "CLASS_NOT_PERMITTED"


# ---- check_soft_preferences ----------------------------------------------


def soft(profile_dict, f, prefs, cfg=CFG):
    return check_soft_preferences(prof(profile_dict, soft_preferences=prefs), cfg, f)


def test_matching_preference_is_info_only(profile_dict):
    r = soft(profile_dict, facts(), [{"kind": "sector", "value": "tech", "weight": 0.8}])
    assert codes(r) == ["PREFERENCE_MATCH"] and r[0].severity == "info" and r[0].measures["weight"] == 0.8


def test_non_matching_preference_is_silent(profile_dict):
    assert soft(profile_dict, facts(), [{"kind": "sector", "value": "Energy"}]) == []


def test_theme_without_data_warns_and_names_substitutes(profile_dict):
    r = soft(profile_dict, facts(ticker="AAA"), [{"kind": "theme", "value": "ESG"}])
    assert codes(r) == ["THEME_DATA_MISSING"] and r[0].severity == "warn"
    assert r[0].measures["substitutes"] == ["ESGV"]


def test_theme_proxy_is_declared_as_substitute_not_as_rating(profile_dict):
    r = soft(profile_dict, facts(ticker="ESGV"), [{"kind": "theme", "value": "esg"}])
    assert codes(r) == ["THEME_PROXY"] and r[0].severity == "info"
    assert "pas une cote" in r[0].message


def test_theme_proxies_come_from_config(profile_dict):
    cfg = GovernanceConfig(theme_proxies={"vert": ["AAA"]})
    assert codes(soft(profile_dict, facts(ticker="AAA"), [{"kind": "theme", "value": "vert"}], cfg)) == ["THEME_PROXY"]


def test_no_preferences_no_reasons(profile_dict):
    assert soft(profile_dict, facts(), []) == []


# ---- check_risk_limits ----------------------------------------------------


def cons(profile_dict, **over):
    return derive_constraints(prof(profile_dict, **over), CFG)  # modere : vol 0,22 ; drawdown 0,30


def test_no_windows_no_reasons(profile_dict):
    assert check_risk_limits(cons(profile_dict), facts()) == []


def test_within_limits_is_silent_and_boundary_is_not_exceeded(profile_dict):
    c = cons(profile_dict)
    f = facts(windows=[window(vol=c.max_volatility, depth=-c.max_drawdown)])
    assert check_risk_limits(c, f) == []


def test_volatility_over_limit_reports_worst_window_with_measures(profile_dict):
    c = cons(profile_dict)
    f = facts(windows=[window("complet", vol=0.30, eid="e1"), window("2022", vol=0.44, eid="e2")])
    (r,) = check_risk_limits(c, f)
    assert r.code == "VOLATILITY_ABOVE_LIMIT" and r.severity == "warn"
    assert r.measures["value"] == 0.44 and r.measures["limit"] == 0.22 and r.measures["window"] == "2022"
    assert r.measures["ratio"] == pytest.approx(2.0)
    assert r.measures["per_window"] == {"complet": 0.30, "2022": 0.44}
    assert r.evidence_ids == ["e1", "e2"]
    assert "44,0 %" in r.message and "22,0 %" in r.message


def test_drawdown_over_limit_uses_absolute_depth(profile_dict):
    c = cons(profile_dict)
    (r,) = check_risk_limits(c, facts(windows=[window(vol=0.05, depth=-0.55)]))
    assert r.code == "DRAWDOWN_ABOVE_LIMIT" and r.measures["value"] == pytest.approx(0.55)


def test_both_limits_exceeded_gives_two_reasons(profile_dict):
    assert codes(check_risk_limits(cons(profile_dict), facts(windows=[window(vol=0.9, depth=-0.9)]))) == [
        "VOLATILITY_ABOVE_LIMIT", "DRAWDOWN_ABOVE_LIMIT",
    ]


def test_risk_limits_never_block(profile_dict):
    assert {r.severity for r in check_risk_limits(cons(profile_dict), facts(windows=[window(vol=5, depth=-0.99)]))} == {"warn"}


# ---- check_horizon_fit ----------------------------------------------------


def test_fast_recovery_fits_horizon(profile_dict):
    w = window(peak=date(2022, 1, 3), recovery=date(2022, 9, 1), depth=-0.2)
    assert check_horizon_fit(prof(profile_dict, horizon_years_min=3), facts(windows=[w])) == []


def test_slow_recovery_exceeds_horizon(profile_dict):
    w = window(peak=date(2021, 11, 29), recovery=date(2023, 5, 18), depth=-0.6)
    (r,) = check_horizon_fit(prof(profile_dict, horizon_years_min=1, horizon_years_max=5), facts(windows=[w]))
    assert r.code == "RECOVERY_LONGER_THAN_HORIZON"
    assert r.measures["recovery_years"] == pytest.approx(1.47, abs=0.01) and r.measures["recovered"] is True


def test_unrecovered_drawdown_is_flagged_as_minimum_duration(profile_dict):
    w = window(peak=date(2022, 1, 3), recovered=False, depth=-0.5, end=date(2023, 12, 29))
    (r,) = check_horizon_fit(prof(profile_dict, horizon_years_min=1, horizon_years_max=5), facts(windows=[w]))
    assert r.measures["recovered"] is False and "minimale" in r.message


def test_no_drawdown_means_no_horizon_finding(profile_dict):
    assert check_horizon_fit(prof(profile_dict), facts(windows=[window(peak=None, depth=0.0)])) == []


def test_horizon_uses_longest_window(profile_dict):
    short = window("2022", peak=date(2022, 1, 3), recovered=False, start=date(2022, 1, 1), end=date(2022, 12, 31), eid="short")
    long_ = window("complet", peak=date(2022, 1, 3), recovery=date(2022, 2, 1), start=date(2021, 11, 12), end=date(2026, 11, 12), eid="long")
    assert check_horizon_fit(prof(profile_dict, horizon_years_min=1, horizon_years_max=5), facts(windows=[short, long_])) == []


# ---- check_data_gaps ------------------------------------------------------


def test_out_of_universe_with_suggestions():
    f = facts("ZST", with_asset=False, suggestions=["ZST.TO"], refusals=[Refusal("asset_lookup", "asset_not_found", "hors univers", "ev_r")])
    (r,) = check_data_gaps(f)
    assert r.code == "OUT_OF_UNIVERSE" and r.severity == "warn" and "ZST.TO" in r.message and r.evidence_ids == ["ev_r"]


def test_metrics_refusal_is_reported():
    f = facts(refusals=[Refusal("compute_metrics", "insufficient_data", "trop peu de prix", "ev_m")])
    (r,) = check_data_gaps(f)
    assert r.code == "METRICS_UNAVAILABLE" and "trop peu de prix" in r.message


def test_known_asset_without_any_metrics_is_a_gap():
    assert codes(check_data_gaps(facts())) == ["NO_METRICS"]


def test_asset_with_metrics_has_no_gap():
    assert check_data_gaps(facts(windows=[window()])) == []


def test_metrics_refusal_is_not_also_reported_as_no_metrics():
    f = facts(refusals=[Refusal("compute_metrics", "insufficient_data", "x", "ev_m")])
    assert codes(check_data_gaps(f)) == ["METRICS_UNAVAILABLE"]


# ---- check_scope_limits ---------------------------------------------------


def test_scope_flags_map_to_refusals_with_alternative_offer():
    out = {r.kind: r for r in check_scope_limits(["asks_prediction", "asks_transaction", "asks_personal_advice"])}
    assert out["prediction"].status == "avertissement"
    assert out["personal_advice"].status == "avertissement"
    assert out["transaction"].status == "bloqué"
    assert all(r.offered_instead for r in out.values())


def test_unknown_or_missing_flags_give_nothing():
    assert check_scope_limits([]) == [] and check_scope_limits(["autre"]) == []


# ---- statuts --------------------------------------------------------------


def _r(sev):
    return Reason(check="risk_limits", code="X", severity=sev, message="m")


def test_status_of_priority():
    assert status_of([]) == "conforme"
    assert status_of([_r("info")]) == "conforme"
    assert status_of([_r("info"), _r("warn")]) == "avertissement"
    assert status_of([_r("warn"), _r("block")]) == "bloqué"


def test_worst_status():
    assert worst_status([]) == "conforme"
    assert worst_status(["conforme", "avertissement"]) == "avertissement"
    assert worst_status(["avertissement", "bloqué", "conforme"]) == "bloqué"
