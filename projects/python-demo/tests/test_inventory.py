import unittest

from inventory import Item, low_stock, total_quantity


class InventoryTests(unittest.TestCase):
    def test_total_quantity(self):
        items = [Item("apple", 2, 3.0), Item("pear", 5, 2.0)]
        self.assertEqual(total_quantity(items), 7)

    def test_low_stock_includes_boundary(self):
        items = [Item("apple", 3, 3.0), Item("pear", 1, 2.0)]
        self.assertEqual(low_stock(items, 3), ["apple", "pear"])
