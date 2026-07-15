"""DoD guard: 8 stageN modules, each stage implements validate(signal, context)."""
import inspect

import pytest

from backend.validation.base import ValidationStage

STAGE_MODULES = [
    ("backend.validation.stage0_data_sanity", "DataSanityStage"),
    ("backend.validation.stage1_regime_gate", "RegimeGateStage"),
    ("backend.validation.stage2_mtf_alignment", "MtfAlignmentStage"),
    ("backend.validation.stage3_volume_confirmation", "VolumeConfirmationStage"),
    ("backend.validation.stage4_volatility_band", "VolatilityBandStage"),
    ("backend.validation.stage5_confluence_score", "ConfluenceScoreStage"),
    ("backend.validation.stage6_event_filter", "EventFilterStage"),
    ("backend.validation.stage7_portfolio_correlation", "PortfolioCorrelationStage"),
]


@pytest.mark.parametrize("module_name,class_name", STAGE_MODULES)
def test_stage_module_exports_validate(module_name, class_name):
    module = __import__(module_name, fromlist=[class_name])
    cls = getattr(module, class_name)
    assert issubclass(cls, ValidationStage)
    sig = inspect.signature(cls.validate)
    assert list(sig.parameters) == ["self", "signal", "context"]
