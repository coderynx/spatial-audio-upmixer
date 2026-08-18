"""Mastering package — post-mixing tonal, dynamic, and loudness processing.

Public API::

    from upmixer.mastering import MasteringChain, MasteringResult
    from upmixer.mastering import ReferenceMatchProcessor

Sub-modules:
    chain            — MasteringChain orchestrator
    match_reference  — ReferenceMatchProcessor (spectral + RMS reference matching)
    eq               — SpectralShaper + EQ_PROFILES
    compressor       — BusCompressor + COMP_PROFILES
    bass             — BassController + BASS_PROFILES
    limiter          — LookAheadLimiter (look-ahead true-peak brickwall limiter)
    delivery         — DELIVERY_TARGETS (named loudness/ceiling specifications)
"""
from .chain import MasteringChain, MasteringResult
from .delivery import DELIVERY_TARGETS, DeliveryTarget, resolve_delivery_target
from .match_reference import ReferenceMatchProcessor

__all__ = [
    "DELIVERY_TARGETS",
    "DeliveryTarget",
    "MasteringChain",
    "MasteringResult",
    "ReferenceMatchProcessor",
    "resolve_delivery_target",
]
