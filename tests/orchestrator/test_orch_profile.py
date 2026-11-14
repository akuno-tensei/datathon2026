import pytest

from alphaforge.orchestrator import InvestorProfile, ProfileError, load_profile, validate_profile


# ---- load_profile ---------------------------------------------------------


def test_load_from_dict(profile_dict):
    p = load_profile(profile_dict())
    assert isinstance(p, InvestorProfile) and p.base_currency == "CAD" and p.language == "fr"


def test_load_passthrough_instance(profile_dict):
    p = InvestorProfile.model_validate(profile_dict())
    assert load_profile(p) is p


@pytest.mark.parametrize("pid", ["amira", "robert", "mateo"])
def test_shipped_example_profiles_load_and_validate(pid):
    p = load_profile(pid)
    assert p.profile_id == pid
    assert validate_profile(p).valid


def test_load_unknown_id():
    with pytest.raises(ProfileError) as e:
        load_profile("personne")
    assert e.value.reason == "profile_not_found"


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "robert.json", "", "x y"])
def test_load_rejects_path_traversal_and_odd_ids(bad):
    with pytest.raises(ProfileError) as e:
        load_profile(bad)
    assert e.value.reason == "invalid_profile_id"


def test_load_schema_invalid_unknown_field(profile_dict):
    with pytest.raises(ProfileError) as e:
        load_profile(profile_dict(persona="robert"))  # extra="forbid" : pas de champ caché
    assert e.value.reason == "profile_schema_invalid"


def test_load_schema_invalid_horizon_order(profile_dict):
    with pytest.raises(ProfileError) as e:
        load_profile(profile_dict(horizon_years_min=10, horizon_years_max=3))
    assert e.value.reason == "profile_schema_invalid"


def test_load_invalid_json_file(tmp_path):
    (tmp_path / "casse.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ProfileError) as e:
        load_profile("casse", profiles_dir=tmp_path)
    assert e.value.reason == "profile_unreadable"


# ---- validate_profile -----------------------------------------------------


def _codes(profile):
    return {i.code for i in validate_profile(profile).issues}


def test_clean_profile_has_no_issue(profile_dict):
    v = validate_profile(load_profile(profile_dict()))
    assert v.valid and v.issues == []


def test_no_objectives_is_warning_not_error(profile_dict):
    v = validate_profile(load_profile(profile_dict(objectives=[])))
    assert v.valid
    assert [(i.code, i.severity) for i in v.issues] == [("NO_OBJECTIVES", "warning")]


def test_high_risk_with_short_horizon_conflicts(profile_dict):
    p = load_profile(profile_dict(risk_tolerance="eleve", horizon_years_min=0, horizon_years_max=1))
    assert "RISK_HORIZON_CONFLICT" in _codes(p)
    assert validate_profile(p).valid  # avertissement : on continue et on le dit


def test_high_risk_with_long_horizon_is_fine(profile_dict):
    assert "RISK_HORIZON_CONFLICT" not in _codes(load_profile(profile_dict(risk_tolerance="eleve", horizon_years_max=20)))


def test_low_risk_vs_high_growth_conflicts(profile_dict):
    p = load_profile(profile_dict(risk_tolerance="faible", objectives=["croissance_elevee"]))
    assert "RISK_OBJECTIVE_CONFLICT" in _codes(p)


def test_high_risk_vs_only_preservation_conflicts(profile_dict):
    p = load_profile(profile_dict(risk_tolerance="eleve", objectives=["preservation_capital"]))
    assert "RISK_OBJECTIVE_CONFLICT" in _codes(p)


def test_exclusion_and_preference_on_same_item_is_error(profile_dict):
    p = load_profile(
        profile_dict(
            hard_exclusions=[{"kind": "sector", "value": "Energy"}],
            soft_preferences=[{"kind": "sector", "value": " energy "}],
        )
    )
    v = validate_profile(p)
    assert not v.valid
    assert any(i.code == "EXCLUSION_PREFERENCE_CONTRADICTION" and i.severity == "error" for i in v.issues)


def test_same_value_different_kind_is_not_a_contradiction(profile_dict):
    p = load_profile(
        profile_dict(
            hard_exclusions=[{"kind": "sector", "value": "energy"}],
            soft_preferences=[{"kind": "theme", "value": "energy"}],
        )
    )
    assert validate_profile(p).valid


def test_preference_weight_bounds(profile_dict):
    with pytest.raises(ProfileError):
        load_profile(profile_dict(soft_preferences=[{"kind": "theme", "value": "esg", "weight": 1.5}]))
