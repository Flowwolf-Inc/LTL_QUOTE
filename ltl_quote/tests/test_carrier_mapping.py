# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ltl_quote.api.carrier_mapping import (
	apply_carrier_response_filter,
	load_carriers_for_rating,
	quote_matches_carrier_filter,
	resolve_rate_source,
)


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

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_source_selects_rate_provider(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(source="SMC3")
		self.assertEqual([doc.name for doc in docs], ["SMC3"])
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_source_list_selects_providers(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(source=["SMC3", "Dayton"])
		self.assertEqual([doc.name for doc in docs], ["SMC3", "DAYTON"])
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_blank_source_list_returns_all_enabled(self, _mock_enabled):
		docs, warnings, available = load_carriers_for_rating(source=[" ", " ", " "])
		self.assertEqual({doc.name for doc in docs}, {"DAYTON", "SMC3", "TFORCE", "ARCB"})
		self.assertEqual(warnings, [])
		self.assertEqual(len(available), 4)

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_empty_source_list_returns_all_enabled(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(source=[])
		self.assertEqual({doc.name for doc in docs}, {"DAYTON", "SMC3", "TFORCE", "ARCB"})
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_source_overrides_carriers_param(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(
			requested=["Dayton"],
			source=["SMC3"],
		)
		self.assertEqual([doc.name for doc in docs], ["SMC3"])
		self.assertEqual(warnings, [])

	@patch("ltl_quote.api.carrier_mapping.get_enabled_carriers", return_value=ENABLED_CARRIERS)
	def test_source_overrides_carrier_preference(self, _mock_enabled):
		docs, warnings, _available = load_carriers_for_rating(
			source="TForce",
			carrier_preference="Dayton",
		)
		self.assertEqual([doc.name for doc in docs], ["TFORCE"])
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


class TestResolveRateSource(unittest.TestCase):
	def test_missing_or_empty_returns_empty_list(self):
		with patch("ltl_quote.api.carrier_mapping.read_request_json", return_value={}):
			self.assertEqual(resolve_rate_source(None), [])
			self.assertEqual(resolve_rate_source(""), [])
			self.assertEqual(resolve_rate_source("  "), [])
			self.assertEqual(resolve_rate_source([]), [])
			self.assertEqual(resolve_rate_source([" ", " ", " "]), [])

	def test_explicit_keyword_argument(self):
		with patch("ltl_quote.api.carrier_mapping.read_request_json", return_value={}):
			self.assertEqual(resolve_rate_source("Dayton"), ["Dayton"])
			self.assertEqual(resolve_rate_source("TForce"), ["TForce"])

	def test_source_list(self):
		with patch("ltl_quote.api.carrier_mapping.read_request_json", return_value={}):
			self.assertEqual(resolve_rate_source(["SMC3", "Dayton"]), ["SMC3", "Dayton"])
			self.assertEqual(resolve_rate_source(["SMC3", " ", "Dayton"]), ["SMC3", "Dayton"])

	def test_payload_mapping(self):
		with patch("ltl_quote.api.carrier_mapping.read_request_json", return_value={}):
			self.assertEqual(resolve_rate_source(None, {"source": "Dayton"}), ["Dayton"])
			self.assertEqual(
				resolve_rate_source(None, json.dumps({"source": ["TForce", "SMC3"]})),
				["TForce", "SMC3"],
			)

	def test_nested_payload_json(self):
		with patch("ltl_quote.api.carrier_mapping.read_request_json", return_value={}):
			self.assertEqual(
				resolve_rate_source(None, {"payload": json.dumps({"source": ["Dayton"]})}),
				["Dayton"],
			)

	def test_get_json_body(self):
		with patch(
			"ltl_quote.api.carrier_mapping.read_request_json",
			return_value={"source": ["TForce"]},
		):
			self.assertEqual(resolve_rate_source(None), ["TForce"])

	def test_keyword_wins_over_json_body(self):
		with patch(
			"ltl_quote.api.carrier_mapping.read_request_json",
			return_value={"source": ["TForce"]},
		):
			self.assertEqual(resolve_rate_source(["Dayton"]), ["Dayton"])


class TestQuoteMatchesCarrierFilter(unittest.TestCase):
	def test_dayton_source_keeps_only_dayton_provider_quotes(self):
		dayton = ENABLED_CARRIERS[0]
		dayton_quote = {
			"carrier_name": "Dayton Freight Lines",
			"carrier_code": "DAYTON",
			"source": "DAYTON",
			"scac": "DAFG",
		}
		smc3_named_dayton = {
			"carrier_name": "Dayton Freight Lines",
			"carrier_code": "DAFG",
			"source": "SMC3",
			"scac": "DAFG",
		}
		tforce_quote = {
			"carrier_name": "TForce Freight",
			"carrier_code": "TFORCE",
			"source": "TFORCE",
		}
		self.assertTrue(quote_matches_carrier_filter(dayton_quote, [dayton]))
		self.assertFalse(quote_matches_carrier_filter(smc3_named_dayton, [dayton]))
		self.assertFalse(quote_matches_carrier_filter(tforce_quote, [dayton]))

	def test_smc3_source_keeps_smc3_quotes_only(self):
		smc3 = ENABLED_CARRIERS[1]
		smc3_quote = {"carrier_name": "Saia LTL Freight", "source": "SMC3", "scac": "SAIA"}
		dayton_quote = {"carrier_name": "Dayton Freight Lines", "source": "DAYTON", "scac": "DAFG"}
		self.assertTrue(quote_matches_carrier_filter(smc3_quote, [smc3]))
		self.assertFalse(quote_matches_carrier_filter(dayton_quote, [smc3]))

	def test_tforce_and_arcbest_are_isolated(self):
		tforce = ENABLED_CARRIERS[2]
		arcb = ENABLED_CARRIERS[3]
		tforce_quote = {"carrier_name": "TForce Freight", "source": "TFORCE"}
		arcb_quote = {"carrier_name": "ArcBest", "source": "ARCBEST"}
		self.assertTrue(quote_matches_carrier_filter(tforce_quote, [tforce]))
		self.assertFalse(quote_matches_carrier_filter(arcb_quote, [tforce]))
		self.assertTrue(quote_matches_carrier_filter(arcb_quote, [arcb]))
		self.assertFalse(quote_matches_carrier_filter(tforce_quote, [arcb]))

	def test_response_filter_drops_other_providers(self):
		dayton = ENABLED_CARRIERS[0]
		quotes = [
			{"carrier_name": "Dayton Freight Lines", "source": "DAYTON", "total_charge": 100},
			{"carrier_name": "Saia LTL Freight", "source": "SMC3", "total_charge": 90},
			{"carrier_name": "TForce Freight", "source": "TFORCE", "total_charge": 110},
		]
		filtered, errors = apply_carrier_response_filter(
			quotes,
			[{"carrier": "TForce Freight", "error": "timeout"}],
			[dayton],
			True,
		)
		self.assertEqual([q["source"] for q in filtered], ["DAYTON"])
		self.assertEqual(errors, [])


