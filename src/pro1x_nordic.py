#!/usr/bin/python3
"""Pro1 X Nordic modifier-map primitives; Linux input ABI, Python 3.8+."""

ORIGINAL = (56, 54, 29, 125, 464, 464)
NORDIC = (56, 42, 29, 125, 100, 54)
ENABLE_ORDER = (5, 1, 4)
RESTORE_ORDER = (1, 4, 5)
LAYOUT = 'pro1x_nordic'
OWNED_STATES = frozenset((ORIGINAL, NORDIC,
                         (56, 54, 29, 125, 464, 54),
                         (56, 42, 29, 125, 464, 54),
                         (56, 54, 29, 125, 100, 54)))


def recover(read_map, write_key):
    """Restore only complete or enumerated package-produced partial states."""
    current = tuple(read_map())
    if current not in OWNED_STATES:
        raise UnsafeState('Refusing recovery of an unknown modifier map')
    if current == ORIGINAL:
        return
    for index in RESTORE_ORDER:
        write_key(index, ORIGINAL[index])
    if tuple(read_map()) != ORIGINAL:
        raise UnsafeState('Recovery readback did not match original map')


class UnsafeState(RuntimeError):
    """The input device is not in a state this package owns."""


def transition(read_map, write_key, enable):
    """Apply the tested order, never silently overwrite unrelated remaps."""
    current = tuple(read_map())
    if current not in (ORIGINAL, NORDIC):
        raise UnsafeState('Unsupported modifier map: {!r}'.format(current))
    desired = NORDIC if enable else ORIGINAL
    if current == desired:
        return
    order = ENABLE_ORDER if enable else RESTORE_ORDER
    try:
        for index in order:
            write_key(index, desired[index])
        if tuple(read_map()) != desired:
            raise UnsafeState('Modifier-map readback did not match the target')
    except Exception:
        rollback_order = RESTORE_ORDER if current == ORIGINAL else ENABLE_ORDER
        try:
            for index in rollback_order:
                write_key(index, current[index])
            if tuple(read_map()) != current:
                raise UnsafeState('Rollback readback did not match the entry state')
        except Exception as rollback_error:
            raise UnsafeState('Modifier-map rollback failed: {}'.format(rollback_error)) from rollback_error
        raise
