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
"""
from .chain import MasteringChain, MasteringResult
from .match_reference import ReferenceMatchProcessor

__all__ = ["MasteringChain", "MasteringResult", "ReferenceMatchProcessor"]
