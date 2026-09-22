"""An alias: the Python reference engine is `factoryforge_sidecar.engine_stub`.

It moved into the sidecar package so that the grader, which is built on it,
can live there too and be frozen with the sidecar (IP-18, IP-08). Everything
that has always said `from engine_stub import EngineStub` with `harness/` on
its path -- the test suite, `tools/drive_engine.py`, `tools/drv_trace.py`, the
sidecar's own `demo` -- keeps working unchanged through this file.

It is an alias and not a re-export, on purpose. Replacing this module in
`sys.modules` with the real one makes `import engine_stub` return *that*
module object: one `EngineStub` class in the process rather than two that fail
`isinstance` against each other, and a monkeypatch through either name lands on
the code that actually runs. The tag-bus parity contract (docs/tag-bus.md) is
this engine's behaviour, and a second copy of it is how the two would drift.
"""

import sys

from factoryforge_sidecar import engine_stub as _module

sys.modules[__name__] = _module
