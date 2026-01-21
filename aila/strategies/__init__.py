# Strategies module
from .ema_pullback import EmaPullbackStrategy
from .ema_pullback_settings import DEFAULT_EMA_PULLBACK_SETTINGS, get_ema_pullback_settings

__all__ = [
    'EmaPullbackStrategy',
    'DEFAULT_EMA_PULLBACK_SETTINGS',
    'get_ema_pullback_settings',
]
