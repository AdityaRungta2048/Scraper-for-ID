"""Versioning for auditable decisions.

Bump MATCHING_ENGINE_VERSION whenever scoring, gating, normalisation or candidate
generation changes in a way that could alter a decision. Cached resolutions are keyed
by this version, so a bump automatically invalidates them.
"""

MATCHING_ENGINE_VERSION = "1.0.1"
