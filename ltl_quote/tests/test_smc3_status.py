# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Status v1 GET /status/v1/app/{SCAC}?proNumber= parser tests."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from ltl_quote.api.smc3 import format_flowwolf_status_events
from ltl_quote.carrier_network.smc3_dispatch import (
	parse_status_events,
	sandbox_status_query_params,
	status_bol_query_params,
	status_pro_query_params,
	status_query_params,
)
from ltl_quote.carrier_network.tracking import highest_shipment_status


SMC3_STATUS_SAMPLE = {
	"transactionId": "7331120f-5f07-4d36-88a6-d704238871c3",
	"scac": "SMCA",
	"referenceNumbers": {"proNumber": "204380071201"},
	"shipmentInfo": {"weight": "702"},
	"origin": {"postalCode": "30250"},
	"destination": {"postalCode": "36801"},
	"transit": {
		"pickupDate": "20260831",
		"delivery": {
			"estimatedDate": "20260903",
			"estimatedTime": "093000",
			"actualDate": "",
			"actualTime": "",
			"signature": "",
			"appointment": {"date": "", "startTime": "", "endTime": "", "notes": ""},
		},
	},
	"status": {
		"code": "IN TRANSIT",
		"date": "20260901",
		"time": "175500",
		"utc": "2026-09-01T22:55:00.000Z",
		"carrierDescription": "Left origin terminal SMC",
		"city": "Atlanta",
		"stateProvince": "GA",
	},
	"statusHistory": [
		{
			"code": "IN TRANSIT",
			"date": "20260901",
			"time": "175500",
			"utc": "2026-09-01T22:55:00.000Z",
			"carrierDescription": "Left origin terminal SMC",
			"city": "Atlanta",
			"stateProvince": "GA",
		},
		{
			"code": "EXCEPTION",
			"date": "20260831",
			"time": "145500",
			"utc": "2026-08-31T18:55:00.000Z",
			"carrierDescription": "Possible delay due to inclement weather in the area",
			"city": "Atlanta",
			"stateProvince": "GA",
		},
		{
			"code": "PICKED UP",
			"date": "20260831",
			"time": "131030",
			"utc": "2026-08-31T17:10:30.000Z",
			"carrierDescription": "Shipment received",
			"city": "Atlanta",
			"stateProvince": "GA",
		},
	],
	"messageStatus": {
		"status": "PASS",
		"code": "10000000",
		"message": "Transaction was successful.",
		"resolution": "",
		"information": [],
	},
}


class TestSMC3Status(unittest.TestCase):
	def test_pro_query_matches_working_endpoint(self):
		pro = "204380071201"
		self.assertEqual(status_pro_query_params(pro), {"proNumber": pro})
		self.assertEqual(
			status_query_params(
				pro,
				{
					"bol_number": "444555678",
					"origin_zip": "30250",
					"destination_zip": "36801",
					"pickup_date": "2026-08-31",
				},
			),
			{"proNumber": pro},
		)

	def test_bol_query_is_fallback_when_pro_missing(self):
		quote_data = {
			"bol_number": "444555678",
			"origin_zip": "30250",
			"destination_zip": "36801",
			"pickup_date": "2026-08-31",
			"origin_country": "USA",
			"destination_country": "USA",
		}
		self.assertEqual(
			status_query_params("", quote_data),
			status_bol_query_params(quote_data),
		)
		self.assertIn("bol", status_bol_query_params(quote_data))

	def test_sandbox_demo_does_not_invent_sample_values(self):
		self.assertEqual(sandbox_status_query_params(), {})
		self.assertEqual(
			sandbox_status_query_params({"status_demo_pro": "204380071201"}),
			{"proNumber": "204380071201"},
		)

	def test_parse_sample_status_history(self):
		events = parse_status_events(SMC3_STATUS_SAMPLE)
		self.assertEqual(len(events), 3)
		codes = [row["status_code"] for row in events]
		self.assertEqual(codes, ["PICKED UP", "EXCEPTION", "IN TRANSIT"])
		self.assertEqual(events[-1]["status_description"], "Left origin terminal SMC")
		self.assertEqual(events[-1]["location"], "Atlanta, GA")
		self.assertEqual(events[-1]["pickup_date"], date(2026, 8, 31))
		self.assertEqual(events[-1]["estimated_delivery"], date(2026, 9, 3))
		self.assertIsInstance(events[-1]["event_datetime"], datetime)

		exception = next(row for row in events if row["status_code"] == "EXCEPTION")
		self.assertEqual(exception["is_exception"], 1)
		self.assertEqual(exception["exception_type"], "Weather")
		self.assertEqual(highest_shipment_status(events), "In Transit")

	def test_does_not_duplicate_current_status(self):
		events = parse_status_events(SMC3_STATUS_SAMPLE)
		in_transit = [row for row in events if row["status_code"] == "IN TRANSIT"]
		self.assertEqual(len(in_transit), 1)

	def test_format_flowwolf_status_events(self):
		events = format_flowwolf_status_events(parse_status_events(SMC3_STATUS_SAMPLE), source="SMC3")
		self.assertEqual(len(events), 3)
		self.assertEqual(
			set(events[-1]),
			{
				"event_datetime",
				"status_code",
				"status_description",
				"location",
				"is_exception",
				"exception_type",
				"source",
			},
		)
		self.assertEqual(events[-1]["status_code"], "IN TRANSIT")
		self.assertEqual(events[-1]["location"], "Atlanta, GA")
		self.assertEqual(events[-1]["source"], "SMC3")
		exception = next(row for row in events if row["status_code"] == "EXCEPTION")
		self.assertEqual(exception["is_exception"], 1)
		self.assertEqual(exception["exception_type"], "Weather")


def run_checks():
	suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSMC3Status)
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	if not result.wasSuccessful():
		raise AssertionError(f"failures={result.failures} errors={result.errors}")
	return {"ok": True, "tests": result.testsRun}
