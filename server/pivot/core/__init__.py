"""Core domain logic for PIVOT.

These modules are pure (no I/O, no heavy native dependencies) so they can be
unit-tested in isolation and reused by the API layer, the DSP engine, the audio
router and the GUI alike:

* :mod:`pivot.core.bands`   — frequency model: region classification and the
  continuous noise/fading-vs-frequency curve (spec §3.1.2, §4.1).
* :mod:`pivot.core.crypto`  — Plain/Cypher reception matrix and simplex
  collision resolution (spec §3.4).
* :mod:`pivot.core.radios`  — radio state and the emergent-net frequency map
  (spec §2, §6.3).
* :mod:`pivot.core.timebase` — UTC storage with configurable display timezone
  (spec §3.8).
"""
