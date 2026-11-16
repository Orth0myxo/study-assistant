"""LOG_LEVEL must actually reach the app's loggers (info lines were invisible)."""

from __future__ import annotations

import logging

import pytest

from app.main import _configure_logging


@pytest.fixture(autouse=True)
def _restore_app_logger():
    app_logger = logging.getLogger("app")
    level, handlers = app_logger.level, list(app_logger.handlers)
    yield
    app_logger.setLevel(level)
    app_logger.handlers[:] = handlers


def test_info_level_is_applied_to_app_loggers():
    _configure_logging("info")

    assert logging.getLogger("app").level == logging.INFO
    assert logging.getLogger("app.services.ingest").isEnabledFor(logging.INFO)


def test_level_name_is_case_insensitive():
    _configure_logging("WARNING")

    assert logging.getLogger("app").level == logging.WARNING


def test_unknown_level_falls_back_to_info():
    _configure_logging("loud")

    assert logging.getLogger("app").level == logging.INFO
