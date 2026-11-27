"""Independent, finite reference model for the MMIO observation experiment.

Public API:
    seed_corpus() -> tuple[int, ...]
    execute(model, variant, initial) -> JSON-compatible execution dictionary
    compare(model, variant, initial) -> JSON-compatible comparison dictionary
    summarize_corpus() -> JSON-compatible aggregate dictionary

Models are ``ram`` and ``read_to_clear``. Variants are ``original``,
``equivalent``, ``second_read``, and ``xor1``. A valid initial value is a
Python integer, excluding bool, in [0, 2**32 - 1]. All returned values are
ordinary JSON types. Logical event records use register names, not host or
target addresses: two status reads followed by one output write. The
read-to-clear state update is internal and is not a software write event.

This model does not read or execute the C fixture. In particular, the
equivalent control is specified directly as selecting the first read; it
does not repeat the fixture's arithmetic implementation.
"""

from __future__ import annotations

import json


WORD_BITS = 32
WORD_MAX = (1 << WORD_BITS) - 1
MODELS = ("ram", "read_to_clear")
VARIANTS = ("original", "equivalent", "second_read", "xor1")
EDGE_SEEDS = (0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF)


def seed_corpus() -> tuple[int, ...]:
    """Exhaust 0..255 and add four 32-bit boundary values (260 total)."""
    return tuple(range(256)) + EDGE_SEEDS


def _validate(model: str, variant: str, initial: int) -> None:
    if not isinstance(model, str):
        raise TypeError("model must be a string")
    if model not in MODELS:
        raise ValueError(f"unknown model: {model!r}")
    if not isinstance(variant, str):
        raise TypeError("variant must be a string")
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant!r}")
    if isinstance(initial, bool) or not isinstance(initial, int):
        raise TypeError("initial state must be an integer, excluding bool")
    if not 0 <= initial <= WORD_MAX:
        raise ValueError("initial state must be in [0, 0xffffffff]")


def execute(model: str, variant: str, initial: int) -> dict:
    """Execute the stated device contract and return one execution record.

    Schema version 1: model, variant, initial_state, reads (two integers),
    output, final_state, and events. Each event contains op (read/write),
    register (status/output), value, and width_bits (32).
    """
    _validate(model, variant, initial)
    state = initial
    reads = []
    events = []
    for _ in range(2):
        value = state
        reads.append(value)
        events.append(
            {"op": "read", "register": "status", "value": value, "width_bits": WORD_BITS}
        )
        if model == "read_to_clear":
            state = 0

    # Specify the observable selection independently of the C control's
    # implementation. In unsigned 32-bit arithmetic, adding then subtracting
    # the same constant selects the original value, including on overflow.
    if variant in ("original", "equivalent"):
        output = reads[0]
    elif variant == "second_read":
        output = reads[1]
    else:  # xor1: a deliberately wrong output for every possible input.
        output = reads[0] ^ 1

    events.append(
        {"op": "write", "register": "output", "value": output, "width_bits": WORD_BITS}
    )
    return {
        "schema_version": 1,
        "model": model,
        "variant": variant,
        "initial_state": initial,
        "reads": reads,
        "output": output,
        "final_state": state,
        "events": events,
    }


def compare(model: str, variant: str, initial: int) -> dict:
    """Compare a variant against the original logical observable trace."""
    original = execute(model, "original", initial)
    patched = execute(model, variant, initial)
    return {
        "schema_version": 1,
        "model": model,
        "variant": variant,
        "initial_state": initial,
        "equivalent": original["events"] == patched["events"],
        "original": original,
        "patched": patched,
    }


def summarize_corpus() -> dict:
    """Run 2,080 finite cases and summarize 1,560 patch comparisons.

    Coverage is exhaustive only over the 8-bit initial-value subdomain.
    The four added values sample 32-bit boundaries; they do not constitute
    exhaustive 32-bit verification.
    """
    seeds = seed_corpus()
    comparison_counts = {
        (model, variant): {"model": model, "variant": variant, "equal": 0, "unequal": 0}
        for model in MODELS
        for variant in VARIANTS[1:]
    }
    execution_count = 0
    comparison_count = 0
    for model in MODELS:
        for initial in seeds:
            executions = {variant: execute(model, variant, initial) for variant in VARIANTS}
            execution_count += len(executions)
            original_events = executions["original"]["events"]
            for variant in VARIANTS[1:]:
                key = "equal" if original_events == executions[variant]["events"] else "unequal"
                comparison_counts[model, variant][key] += 1
                comparison_count += 1
    return {
        "schema_version": 1,
        "word_bits": WORD_BITS,
        "seed_count": len(seeds),
        "seed_domain": {
            "exhaustive_min": 0,
            "exhaustive_max": 255,
            "additional_seeds": list(EDGE_SEEDS),
        },
        "execution_count": execution_count,
        "comparison_count": comparison_count,
        "comparisons": list(comparison_counts.values()),
    }


if __name__ == "__main__":
    print(json.dumps(summarize_corpus(), indent=2, sort_keys=True))
