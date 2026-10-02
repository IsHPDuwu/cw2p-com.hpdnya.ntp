"""Compatibility export; runtime imports ntp_config to avoid generic module collisions."""
from ntp_config import NtpConfig

__all__ = ["NtpConfig"]