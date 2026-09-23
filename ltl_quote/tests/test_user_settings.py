# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ltl_quote.api.user_settings import (
	STANDARD_ACCESSORIALS,
	_seed_accessorials,
	_seed_quote_sources,
	accessorial_options_for_form,
)


class _ChildList(list):
	def append(self, value):
		super().append(SimpleNamespace(**value) if isinstance(value, dict) else value)


class _Settings:
	def __init__(self):
		self.quote_sources = _ChildList()
		self.accessorials = _ChildList()

	def append(self, field, value):
		getattr(self, field).append(value)


class TestUserSettingsSeeding(unittest.TestCase):
	def test_missing_settings_seed_enabled_carriers_and_standard_accessorials(self):
		doc = _Settings()
		carriers = [
			SimpleNamespace(name="DAYTON", carrier_name="Dayton Freight Lines"),
			SimpleNamespace(name="TFORCE", carrier_name="TForce Freight"),
		]
		master = {
			code: SimpleNamespace(name=code, accessorial_code=code, accessorial_name=label)
			for group in STANDARD_ACCESSORIALS.values()
			for code, label in group
		}
		with patch("ltl_quote.api.user_settings._globally_enabled_carriers", return_value=carriers):
			self.assertTrue(_seed_quote_sources(doc))
		with patch("ltl_quote.api.user_settings._master_accessorial_map", return_value=master):
			self.assertTrue(_seed_accessorials(doc))

		self.assertEqual({row.carrier for row in doc.quote_sources}, {"DAYTON", "TFORCE"})
		self.assertTrue(all(row.enabled == 1 for row in doc.quote_sources))
		seeded = {(row.accessorial_code, row.service_group) for row in doc.accessorials}
		expected = {(code, group) for group, entries in STANDARD_ACCESSORIALS.items() for code, _ in entries}
		self.assertEqual(seeded, expected)
		self.assertTrue(all(row.show_on_form == 1 for row in doc.accessorials))
		self.assertTrue(all(row.default_selected == 0 for row in doc.accessorials))

	def test_seeding_does_not_reenable_user_disabled_carrier(self):
		doc = _Settings()
		doc.append("quote_sources", {"carrier": "DAYTON", "carrier_name": "Dayton", "enabled": 0})
		carriers = [SimpleNamespace(name="DAYTON", carrier_name="Dayton Freight Lines")]
		with patch("ltl_quote.api.user_settings._globally_enabled_carriers", return_value=carriers):
			self.assertFalse(_seed_quote_sources(doc))
		self.assertEqual(doc.quote_sources[0].enabled, 0)


class TestAccessorialFormOptions(unittest.TestCase):
	def test_options_honor_show_and_default_selected(self):
		prefs = {
			"pickup": [
				{
					"code": "LIFTGATE",
					"label": "Liftgate Pickup",
					"master_name": "Liftgate Service",
					"show_on_form": 1,
					"default_selected": 1,
				},
				{
					"code": "INSIDE_DELIVERY",
					"label": "Inside Pickup",
					"master_name": "Inside Delivery",
					"show_on_form": 0,
					"default_selected": 1,
				},
			],
			"delivery": [
				{
					"code": "RESIDENTIAL",
					"label": "Residential Delivery",
					"master_name": "Residential Delivery",
					"show_on_form": 1,
					"default_selected": 0,
				}
			],
			"load": [],
		}
		with patch("ltl_quote.api.user_settings.accessorial_preference_list", return_value=prefs):
			options = accessorial_options_for_form()

		self.assertEqual([row["code"] for row in options["pickup"]], ["LIFTGATE"])
		self.assertEqual(options["pickup"][0]["default_selected"], 1)
		self.assertEqual([row["code"] for row in options["delivery"]], ["RESIDENTIAL"])
		self.assertEqual(options["delivery"][0]["default_selected"], 0)
		self.assertEqual(options["load"], [])
