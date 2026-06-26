"""skilltrain — ReflACT-discipline skill optimization (ADR-001 / Option B).

Pure, dependency-light building blocks for the disciplined skill-optimization
loop. These modules are token-free and side-effect-free by design: every
mechanism is a pure function/dataclass over injected rollout scores and typed
edits, so it is unit-tested with ZERO real OCR (test catalog §0).

P1 wires these into run_orchestrator; until then nothing here touches the live
iteration loop (zero blast radius).
"""
