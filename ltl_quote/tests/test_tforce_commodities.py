# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest

from ltl_quote.carrier_network.adapters.tforce import TForceCarrierAdapter


class TestTForceCommodities(unittest.TestCase):
	def test_class_is_string_not_float(self):
		row = TForceCarrierAdapter._commodity_from_item(
			{"freight_class": "70", "weight": 1450, "qty": 1}
		)
		self.assertIsInstance(row["class"], str)
		self.assertEqual(row["class"], "70")

	def test_half_class_stays_canonical_string(self):
		row = TForceCarrierAdapter._commodity_from_item(
			{"classification": 77.5, "weight": 500, "qty": 1}
		)
		self.assertEqual(row["class"], "77.5")
		self.assertNotIsInstance(row["class"], float)
