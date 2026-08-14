"""Bounded numeric argparse types shared across the CLI argument modules."""

import argparse


def positive_int(value: str, option: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"{option} must be at least 1")
    return parsed


def positive_float(value: str, option: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError(f"{option} must be greater than 0")
    return parsed
