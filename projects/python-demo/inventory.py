"""Inventory helpers for the demo project."""

from dataclasses import dataclass


@dataclass
class Item:
    name: str
    quantity: int
    price: float


def total_quantity(items):
    total = 0
    for item in items:
        total += item.quantity
    return total


def low_stock(items, threshold=3):
    # BUG: an item with exactly `threshold` units should be reported too.
    return [item.name for item in items if item.quantity < threshold]


def find_item(items, name):
    for item in items:
        if item.name == name:
            return item
    raise KeyError(name)
