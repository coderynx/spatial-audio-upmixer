"""Aggregation and formatting for evaluation harness results."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from upmixer.eval.harness import RunSettings


@dataclass
class StemScore:
    """Scores for one stem on one corpus item."""

    stem: str
    category: str
    sdr: float
    fullness: float
    bleedless: float


@dataclass
class EvalReport:
    """Full result of an evaluation run: settings plus per-item scores."""

    settings: "RunSettings"
    scores: list[StemScore]

    def by_stem(self) -> dict[str, tuple[float, float, float]]:
        """Mean (sdr, fullness, bleedless) grouped by canonical stem name."""
        return _grouped_means(self.scores, key=lambda s: s.stem)

    def by_category(self) -> dict[str, tuple[float, float, float]]:
        """Mean (sdr, fullness, bleedless) grouped by regression-probe category."""
        return _grouped_means(self.scores, key=lambda s: s.category)


def _grouped_means(scores: list[StemScore], key) -> dict[str, tuple[float, float, float]]:
    groups: dict[str, list[StemScore]] = defaultdict(list)
    for score in scores:
        groups[key(score)].append(score)
    return {
        group: (
            sum(s.sdr for s in items) / len(items),
            sum(s.fullness for s in items) / len(items),
            sum(s.bleedless for s in items) / len(items),
        )
        for group, items in groups.items()
    }


def format_report(report: EvalReport) -> str:
    """Render a report as a per-stem, per-category text table.

    Always reports SDR, fullness, and bleedless together (never SDR alone),
    and prefixes the table with the exact settings that produced it.
    """
    lines = [
        "Settings: model={model} sample_rate={sample_rate} segment_size={segment_size} "
        "overlap={overlap} batch_size={batch_size} ensemble_algorithm={ensemble_algorithm} "
        "ensemble_models={ensemble_models}".format(**vars(report.settings)),
        "",
        "Per-stem (mean SDR dB / fullness / bleedless):",
    ]
    for stem, (mean_sdr, mean_fullness, mean_bleedless) in sorted(report.by_stem().items()):
        lines.append(f"  {stem:<16} SDR={mean_sdr:7.2f}  fullness={mean_fullness:.3f}  bleedless={mean_bleedless:.3f}")

    lines.append("")
    lines.append("Per-category (mean SDR dB / fullness / bleedless):")
    for category, (mean_sdr, mean_fullness, mean_bleedless) in sorted(report.by_category().items()):
        lines.append(f"  {category:<16} SDR={mean_sdr:7.2f}  fullness={mean_fullness:.3f}  bleedless={mean_bleedless:.3f}")

    return "\n".join(lines)
