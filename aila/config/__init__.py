"""
AILA - Configuration Module

Provides configuration management for the application.
"""

from .settings import AppSettings, get_settings, settings

__all__ = ["AppSettings", "get_settings", "settings"]
