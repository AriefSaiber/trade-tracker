"""scoring.py: weighted confluence score 0-100, weights from configs/validation.yaml."""
from backend.core.config import load_yaml_config
from backend.validation.scoring import load_weights, weighted_confluence_score


def test_weights_come_from_validation_yaml():
    weights = load_weights()
    yaml_weights = load_yaml_config("validation").get("confluence_score.weights")
    assert weights == {k: float(v) for k, v in yaml_weights.items()}
    assert sum(weights.values()) > 0


def test_perfect_components_score_100():
    weights = load_weights()
    assert weighted_confluence_score({k: 1.0 for k in weights}, weights) == 100.0


def test_zero_components_score_0():
    weights = load_weights()
    assert weighted_confluence_score({k: 0.0 for k in weights}, weights) == 0.0


def test_partial_score_is_weighted_sum():
    weights = {"a": 30.0, "b": 70.0}
    assert weighted_confluence_score({"a": 1.0, "b": 0.5}, weights) == 65.0


def test_components_clamped_and_missing_treated_as_zero():
    weights = {"a": 50.0, "b": 50.0}
    assert weighted_confluence_score({"a": 2.0}, weights) == 50.0
    assert weighted_confluence_score({}, weights) == 0.0


def test_empty_weights_score_0():
    assert weighted_confluence_score({"a": 1.0}, {}) == 0.0
