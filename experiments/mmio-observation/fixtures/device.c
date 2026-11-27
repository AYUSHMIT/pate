/*
 * Minimal, freestanding 32-bit observation fixture.
 *
 * AUDIT_MODEL:   0 = ordinary RAM, 1 = explicit read-to-clear C model.
 * AUDIT_VARIANT: 0 = original (first read), 1 = equivalent unsigned identity,
 *               2 = second read, 3 = deliberately wrong first-read XOR 1.
 * AUDIT_SEED:    uint32_t constant, default 1, used by audit_entry().
 * AUDIT_HOST:    define to include audit_run(initial) for native ctypes use.
 *
 * The read-to-clear stores below represent internal model state updates.
 * They are not claimed to be software-issued writes to a physical device.
 * Every variant performs two sequenced volatile status reads and then one
 * observable output write. Disassembly must still be checked for each
 * compiler/build configuration used as evidence.
 *
 * GCC/Clang's fixed-width type avoids a dependency on cross-target libc
 * headers. The experiment requires a target with a 32-bit uint32 type.
 */

#if !defined(__GNUC__) && !defined(__clang__)
#error "This fixture requires GCC or Clang attributes and fixed-width types"
#endif

typedef __UINT32_TYPE__ uint32_t;
_Static_assert(sizeof(uint32_t) == 4, "The fixture requires a 32-bit uint32_t");

#ifndef AUDIT_MODEL
#define AUDIT_MODEL 0
#endif
#ifndef AUDIT_VARIANT
#define AUDIT_VARIANT 0
#endif
#ifndef AUDIT_SEED
#define AUDIT_SEED 1u
#endif

#if AUDIT_MODEL != 0 && AUDIT_MODEL != 1
#error "AUDIT_MODEL must be 0 (RAM) or 1 (read-to-clear)"
#endif
#if AUDIT_VARIANT < 0 || AUDIT_VARIANT > 3
#error "AUDIT_VARIANT must be in [0, 3]"
#endif

volatile uint32_t audit_state
    __attribute__((section(".audit_state"), aligned(4), used)) = 0u;
volatile uint32_t audit_output
    __attribute__((section(".output"), aligned(4), used)) = 0u;

static __attribute__((always_inline)) inline uint32_t audit_read_status(void)
{
    const uint32_t value = audit_state;
#if AUDIT_MODEL == 1
    audit_state = 0u;
#endif
    return value;
}

static __attribute__((always_inline)) inline uint32_t audit_sequence(void)
{
    const uint32_t first = audit_read_status();
    const uint32_t second = audit_read_status();
    uint32_t selected;

    /* Both initializers retain their volatile side effects in every variant. */
    (void) first;
    (void) second;
#if AUDIT_VARIANT == 0
    selected = first;
#elif AUDIT_VARIANT == 1
    const uint32_t intermediate = first + 7u;
    selected = intermediate - 7u;
#elif AUDIT_VARIANT == 2
    selected = second;
#else
    selected = first ^ 1u;
#endif
    audit_output = selected;
    return selected;
}

__attribute__((noinline, used)) void audit_entry(void)
{
    /* This initializes the executable model; it is not a hardware driver write. */
    audit_state = (uint32_t) AUDIT_SEED;
    (void) audit_sequence();
}

#ifdef AUDIT_HOST
__attribute__((noinline, used)) uint32_t audit_run(uint32_t initial)
{
    audit_state = initial;
    return audit_sequence();
}
#endif
