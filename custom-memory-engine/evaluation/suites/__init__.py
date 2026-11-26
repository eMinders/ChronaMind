"""Synthetic evaluation suites (W4: null suite C-012 / Gate G4)."""

from evaluation.suites.admission_calibration import (
    MAX_NULL_FAR,
    AdmissionCalibrationReport,
    StratumResult,
    run_admission_calibration,
)

__all__ = [
    "MAX_NULL_FAR",
    "AdmissionCalibrationReport",
    "StratumResult",
    "run_admission_calibration",
]
