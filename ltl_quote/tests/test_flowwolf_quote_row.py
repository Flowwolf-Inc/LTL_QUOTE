# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ltl_quote.api.flowwolf import (
	_ensure_smc3_quote_line_for_id,
	_parse_smc3_composite_quote_id,
	_quote_line_matches_id,
	_resolve_quote_row_index,
)


SMC3_PIPE_ID = "DLDS|DYNAMIC|STND|sandbox-standin"


def _row(**kwargs):
	defaults = {
		"carrier": "SMC3",
		"carrier_quote_id": "",
		"quoted_scac": "",
		"rate_source": "SMC3",
	}
	defaults.update(kwargs)
	return SimpleNamespace(**defaults)


class TestSmc3CompositeQuoteId(unittest.TestCase):
	def test_parses_sandbox_standin_id(self):
		parsed = _parse_smc3_composite_quote_id(SMC3_PIPE_ID)
		self.assertEqual(parsed["scac"], "DLDS")
		self.assertEqual(parsed["pricing_type"], "DYNAMIC")
		self.assertEqual(parsed["service_level"], "STND")
		self.assertEqual(parsed["quote_id"], "sandbox-standin")

	def test_ignores_dayton_and_tforce_ids(self):
		self.assertIsNone(_parse_smc3_composite_quote_id("DAY-188846431"))
		self.assertIsNone(_parse_smc3_composite_quote_id("TFF-001943558"))


class TestQuoteLineMatching(unittest.TestCase):
	def test_exact_pipe_id(self):
		row = _row(carrier_quote_id=SMC3_PIPE_ID, quoted_scac="DLDS")
		self.assertTrue(_quote_line_matches_id(row, SMC3_PIPE_ID))

	def test_matches_smc3_line_by_scac_when_id_differs(self):
		row = _row(carrier_quote_id="DLDS|CONTRACT|STND|other", quoted_scac="DLDS")
		self.assertTrue(_quote_line_matches_id(row, SMC3_PIPE_ID))

	def test_does_not_match_tforce_line_for_smc3_pipe_id(self):
		row = _row(
			carrier="TFORCE",
			carrier_quote_id="TFF-001943558",
			quoted_scac="TFF",
			rate_source="",
		)
		self.assertFalse(_quote_line_matches_id(row, "TFF|DYNAMIC|STND|sandbox-standin"))


class TestResolveQuoteRowIndex(unittest.TestCase):
	def _quote(self, rows):
		return SimpleNamespace(carrier_quotes=rows, selected_carrier_quote=None)

	def test_picks_smc3_pipe_id_among_direct_carriers(self):
		quote = self._quote(
			[
				_row(carrier="TFORCE", carrier_quote_id="TFF-001943558", quoted_scac="TFFA", rate_source=""),
				_row(carrier="DAYTON", carrier_quote_id="DAY-194466156", quoted_scac="DAFG", rate_source=""),
				_row(carrier_quote_id=SMC3_PIPE_ID, quoted_scac="DLDS"),
			]
		)
		self.assertEqual(_resolve_quote_row_index(quote, carrier_quote_id=SMC3_PIPE_ID), 2)

	@patch("ltl_quote.api.flowwolf.frappe")
	def test_missing_pipe_id_throws(self, mock_frappe):
		def _throw(msg, *args, **kwargs):
			raise RuntimeError(msg)

		mock_frappe.throw.side_effect = _throw
		quote = self._quote(
			[
				_row(carrier="TFORCE", carrier_quote_id="TFF-001943558", quoted_scac="TFFA", rate_source=""),
				_row(carrier="DAYTON", carrier_quote_id="DAY-194466156", quoted_scac="DAFG", rate_source=""),
			]
		)
		with self.assertRaises(RuntimeError) as ctx:
			_resolve_quote_row_index(quote, carrier_quote_id=SMC3_PIPE_ID)
		self.assertIn("No quote line found", str(ctx.exception))


class TestEnsureSmc3QuoteLine(unittest.TestCase):
	@patch(
		"ltl_quote.api.flowwolf._prior_smc3_quote_line_values",
		return_value={"total_charge": 387, "carrier_name": "Diamond Line Delivery"},
	)
	@patch("ltl_quote.api.flowwolf._smc3_carrier_doc_id", return_value="SMC3")
	def test_appends_missing_dlds_line(self, _carrier_id, _prior):
		appended = []
		quotes = []

		def _append(_table, row):
			appended.append(row)
			quotes.append(_row(**row))

		quote = SimpleNamespace(
			carrier_quotes=quotes,
			currency="USD",
			append=_append,
			save=lambda **_kwargs: None,
			reload=lambda: None,
		)
		self.assertTrue(_ensure_smc3_quote_line_for_id(quote, SMC3_PIPE_ID))
		self.assertEqual(appended[0]["carrier"], "SMC3")
		self.assertEqual(appended[0]["quoted_scac"], "DLDS")
		self.assertEqual(appended[0]["carrier_quote_id"], SMC3_PIPE_ID)
		self.assertEqual(appended[0]["carrier_name"], "Diamond Line Delivery")

	@patch("ltl_quote.api.flowwolf._smc3_carrier_doc_id", return_value="SMC3")
	def test_noop_when_line_already_present(self, _carrier_id):
		quote = SimpleNamespace(
			carrier_quotes=[_row(carrier_quote_id=SMC3_PIPE_ID, quoted_scac="DLDS")],
			append=lambda *_args, **_kwargs: self.fail("should not append"),
			save=lambda **_kwargs: None,
			reload=lambda: None,
		)
		self.assertFalse(_ensure_smc3_quote_line_for_id(quote, SMC3_PIPE_ID))
