"""Checkout helpers for the demo project."""

from inventory import find_item


def apply_discount(price, percent):
    # BUG: percent is a percentage (10 means 10%), not an absolute amount.
    return price - percent


def add_note(note, notes=[]):
    # BUG: the default list is shared between calls.
    notes.append(note)
    return notes


def line_total(items, name, count=1):
    item = find_item(items, name)
    return apply_discount(item.price * count, 0)
