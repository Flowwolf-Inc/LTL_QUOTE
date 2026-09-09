# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ltl_quote.api.carrier_mapping import load_carriers_for_rating


def _carrier(name: str, carrier_name: str, connector_type: str, code: str | None = None):
	return SimpleNamespace(
		name=name,
		carrier_code=code or name,
		carrier_name=carrier_name,
		connector_type=connector_type,
	)


ENABLED_CARRIERS = [
	_carrier("DAYTON", "Dayton Freight Lines", "Dayton"),
	_carrier("SMC3", "SMC3", "SMC3"),
	_carrier("TFORCE", "TForce Freight", "TForce"),
	_carrier("ARCB", "ArcBest", "ArcBest API"),
]


class TestLoadCarriersForRating(unittest.TestCase):
	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_empty_param_returns_all_enabled(self, _mock_enabled):
		docs, warnings, available = load_carriers_for_rating(requested=None)
		self.assertEqual({doc.name for doc in docs}, {"DAYTON", "SMC3", "TFORCE", "ARCB"})
		self.assertEqual(warnings, [])
		self.assertEqual(len(available), 4)
		self.assertEqual({row["id"] for row in available}, {"DAYTON", "SMC3", "TFORCE", "ARCB"})
		for row in available:
			self.assertIn("id", row)
			self.assertIn("name", row)
			self.assertIn("connector_type", row)

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_alias_and_comma_string_resolution(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(requested="DAYTON, SMC3")
		self.assertEqual([doc.name for doc in docs], ["DAYTON", "SMC3"])
		self.assertEqual({doc.carrier_name for doc in docs}, {"Dayton Freight Lines", "SMC3"})
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_tforce_smc3_excludes_dayton(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(requested="Tforce,SMC3")
		self.assertEqual([doc.name for doc in docs], ["TFORCE", "SMC3"])
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_combination_aliases(self, _mock_enabled):
		cases = {
			"Dayton": {"DAYTON"},
			"TForce": {"TFORCE"},
			"SMC3": {"SMC3"},
			"ArcBest": {"ARCB"},
			"ArcBest,TForce": {"ARCB", "TFORCE"},
			"ArcBest,SMC3": {"ARCB", "SMC3"},
			"ArcBest,Dayton": {"ARCB", "DAYTON"},
			"TForce,SMC3": {"TFORCE", "SMC3"},
			"TForce,Dayton": {"TFORCE", "DAYTON"},
			"SMC3,Dayton": {"SMC3", "DAYTON"},
			"ArcBest,TForce,SMC3": {"ARCB", "TFORCE", "SMC3"},
			"ArcBest,TForce,Dayton": {"ARCB", "TFORCE", "DAYTON"},
			"ArcBest,SMC3,Dayton": {"ARCB", "SMC3", "DAYTON"},
			"TForce,SMC3,Dayton": {"TFORCE", "SMC3", "DAYTON"},
			"ArcBest,TForce,SMC3,Dayton": {"ARCB", "TFORCE", "SMC3", "DAYTON"},
		}
		for raw, expected in cases.items():
			docs, warnings, _available = load_carriers_for_rating(requested=raw)
			self.assertEqual({doc.name for doc in docs}, expected, raw)
			self.assertEqual(warnings, [], raw)

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_unknown_or_disabled_skipped_with_warning(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(requested=["Dayton", "FakeCarrier"])
		self.assertEqual([doc.name for doc in docs], ["DAYTON"])
		self.assertEqual(len(warnings), 1)
		self.assertEqual(warnings[0]["carrier"], "FakeCarrier")
		self.assertIn("FakeCarrier", warnings[0]["error"])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_carriers_param_overrides_carrier_preference(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(
			requested=["SMC3"],
			carrier_preference="Dayton",
		)
		self.assertEqual([doc.name for doc in docs], ["SMC3"])
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=[])
	def test_empty_enabled_returns_no_docs_or_available(self, _mock_enabled):
		from ltl_quote.api.carrier_mapping import (
			NO_ENABLED_CARRIERS_MESSAGE,
			enabled_carrier_options,
			require_enabled_carriers,
		)

		docs, warnings, available = load_carriers_for_rating(requested=None)
		self.assertEqual(docs, [])
		self.assertEqual(warnings, [])
		self.assertEqual(available, [])
		self.assertEqual(enabled_carrier_options(), [])
		with patch("ltl_quote.api.carrier_mapping.frappe.throw") as throw:
			require_enabled_carriers(available)
			throw.assert_called_once_with(NO_ENABLED_CARRIERS_MESSAGE)
