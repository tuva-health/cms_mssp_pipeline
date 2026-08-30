"""Compatibility shim for processing config.

The project now uses mssp_pipeline.config as the single source of truth for
runtime settings. This module re-exports those values so existing imports of
mssp_pipeline.processing.config continue to work.
"""

from mssp_pipeline.config import *  # noqa: F403,F401
