"""Unified assets-based manifest for upmix jobs.

Schema
------
Every manifest must declare a ``version`` and an ``assets`` list::

    version: "1.0.0"

    # Optional informational block — not inherited by assets
    metadata:
      name: "My Project"
      author: "Jane Doe"
      description: "..."

    # Global pipeline blocks (inherited by every asset unless overridden)
    engine:
      mode: stem          # or realtime
      stem_cache_dir: /tmp/upmixer_stems
    mixing:
      channel_layout: 7.1.4
      stem_rebalance:
        Vocals: +1.5
    mastering:
      loudness:
        normalize: true
        target: -18.0
    routing:
      center_gain: 0.85
    format:
      type: adm-bwf
      subtype: PCM_24
      sample_rate: 48000

    # Assets — single file or batch (uniform treatment)
    assets:
      - input: tracks/01.flac
        output: dist/01.wav
        stem_cache_dir: /tmp/stems/01   # asset-level shortcut

      - input: tracks/02.flac
        output: dist/02.wav
        mixing:                         # asset-level block override (deep-merged)
          stem_rebalance:
            Vocals: +0.0

Versioning
----------
``version`` must match ``MAJOR.MINOR`` or ``MAJOR.MINOR.PATCH`` (SemVer-like).
Missing or malformed versions raise :class:`ManifestError`.

Extensibility
-------------
Modules can register their own YAML block keys without modifying this file::

    from upmixer.manifest import register_block_keys

    register_block_keys('mixing', {
        'reverb': {
            'room_size': ('config', 'reverb_room_size'),
            'wet':       ('config', 'reverb_wet'),
        }
    })

See :func:`register_block` and :func:`register_block_keys`.

Priority
--------
CLI flags > per-asset manifest values > global manifest values > UpmixConfig defaults.

Package layout
--------------
This is a package, not a single module: ``schema.py`` holds the block
registry and dataclasses, ``validate.py`` validates a raw manifest dict, and
``load.py`` loads/parses/applies one. Every name below is re-exported here so
``from upmixer.manifest import X`` keeps working regardless of which
sub-module actually defines ``X``.
"""
from __future__ import annotations

from upmixer.manifest.load import (  # noqa: F401
    apply_asset_job,
    load_manifest,
    parse_manifest,
)
from upmixer.manifest.schema import (  # noqa: F401
    AssetJob,
    BlockMapping,
    ManifestError,
    ManifestMeta,
    _BLOCK_REGISTRY,
    _FIELD_MAP,
    list_manifest_keys,
    manifest_parameter_schema,
    register_block,
    register_block_keys,
)
from upmixer.manifest.validate import validate_manifest  # noqa: F401
