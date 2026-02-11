"""Unit tests for inference telemetry helpers."""

from kura_workers.inference_telemetry import (
    INFERENCE_ERROR_ENGINE_UNAVAILABLE,
    INFERENCE_ERROR_INSUFFICIENT_DATA,
    INFERENCE_ERROR_NUMERIC_INSTABILITY,
    INFERENCE_ERROR_UNEXPECTED,
    classify_inference_error,
)


def test_classify_insufficient_data_error():
    err = RuntimeError("insufficient data: required_points=5 observed_points=3")
    assert classify_inference_error(err) == INFERENCE_ERROR_INSUFFICIENT_DATA


def test_classify_numeric_instability_error():
    err = RuntimeError("matrix is singular and produced NaN coefficients")
    assert classify_inference_error(err) == INFERENCE_ERROR_NUMERIC_INSTABILITY


def test_classify_engine_unavailable_error():
    err = ImportError("No module named 'pymc'")
    assert classify_inference_error(err) == INFERENCE_ERROR_ENGINE_UNAVAILABLE


def test_classify_unexpected_error():
    err = RuntimeError("socket closed unexpectedly")
    assert classify_inference_error(err) == INFERENCE_ERROR_UNEXPECTED
