# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ltl_quote.api.sync import (
	EVENT_HANDLERS,
	serialize_carrier_quote,
	should_skip_outbound,
)
from ltl_quote.integrations.revenova.fields import CARRIER_QUOTE_CUSTOM_FIELDS


def _doc(**kwargs):
	defaults = {
		"name": "CQ-2026-00001",
		"synced_from_revenova": 0,
		"revenova_id": "a0B000000000001",
		"quote_request": "LTL-QR-2026-00382",
		"carrier": "SMC3",
		"carrier_name": "Diamond Line Delivery",
		"quoted_scac": "DLDS",
		"carrier_quote_id": "DLDS|DYNAMIC|STND|sandbox-standin",
		"service_level": "STND",
		"status": "Quoted",
		"total_charge": 387,
		"currency": "USD",
		"transit_days": 2,
		"origin_zip": "45414",
		"destination_zip": "60601",
		"flags": SimpleNamespace(),
	}
	defaults.update(kwargs)
	return SimpleNamespace(**defaults)


class TestOutboundLoopPrevention(unittest.TestCase):
	def setUp(self):
		self.flags_patch = patch("ltl_quote.api.sync.frappe.flags", SimpleNamespace(in_revenova_sync=False))
		self.flags_patch.start()
		self.addCleanup(self.flags_patch.stop)

	def test_skips_when_synced_from_revenova_check_is_set(self):
		doc = _doc(synced_from_revenova=1)
		self.assertTrue(should_skip_outbound(doc))

	def test_skips_when_inbound_flag_is_set(self):
		doc = _doc(synced_from_revenova=0)
		doc.flags.synced_from_revenova = True
		self.assertTrue(should_skip_outbound(doc))

	def test_skips_when_ignore_flag_is_set(self):
		doc = _doc(synced_from_revenova=0)
		doc.flags.ignore_revenova_sync = True
		self.assertTrue(should_skip_outbound(doc))

	def test_allows_local_edit(self):
		doc = _doc(synced_from_revenova=0)
		self.assertFalse(should_skip_outbound(doc))


class TestEventRouting(unittest.TestCase):
	def test_single_endpoint_routes_known_events(self):
		self.assertIn("carrier_quote", EVENT_HANDLERS)
		self.assertIn("bol", EVENT_HANDLERS)
		self.assertIn("shipment_status", EVENT_HANDLERS)

	def test_placeholders_return_ignored(self):
		self.assertEqual(EVENT_HANDLERS["bol"]({}).get("status"), "ignored")
		self.assertEqual(EVENT_HANDLERS["shipment_status"]({}).get("status"), "ignored")


class TestSerializeCarrierQuote(unittest.TestCase):
	def test_outbound_payload_shape(self):
		payload = serialize_carrier_quote(_doc())
		self.assertEqual(payload["frappe_name"], "CQ-2026-00001")
		self.assertEqual(payload["revenova_id"], "a0B000000000001")
		self.assertEqual(payload["quoted_scac"], "DLDS")
		self.assertEqual(payload["total_charge"], 387)


class TestCustomFieldDefinition(unittest.TestCase):
	def test_revenova_id_is_unique_data_field(self):
		revenova_id = next(row for row in CARRIER_QUOTE_CUSTOM_FIELDS if row["fieldname"] == "revenova_id")
		self.assertEqual(revenova_id["fieldtype"], "Data")
		self.assertEqual(revenova_id["unique"], 1)

	def test_synced_from_revenova_is_check(self):
		flag = next(row for row in CARRIER_QUOTE_CUSTOM_FIELDS if row["fieldname"] == "synced_from_revenova")
		self.assertEqual(flag["fieldtype"], "Check")
		self.assertEqual(flag["default"], "0")
