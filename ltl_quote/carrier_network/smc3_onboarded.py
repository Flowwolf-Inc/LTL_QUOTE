# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 EVA Carriers Onboarded Matrix — SCAC → display name and pricing modes.

Maps carrier SCACs from the SMC3 EVA onboarded matrix to clean primary
display names. Used to seed LTL Carrier SMC3 network rows and to label
Available Carrier Rates without replacing the carrier name with "SMC3".
"""

from __future__ import annotations

from typing import TypedDict


class OnboardedCarrier(TypedDict):
	scac: str
	name: str
	contract: bool
	dynamic: bool
	sandbox: bool


# (scac, display name, contract, dynamic, sandbox)
_MATRIX_ROWS: list[tuple[str, str, bool, bool, bool]] = [
	("SMCA", "SMC3 Demo Carrier", True, True, True),
	("ODFL", "Old Dominion Freight Line", True, True, False),
	("SAIA", "Saia LTL Freight", True, True, False),
	("EXLA", "Estes Express Lines", True, True, False),
	("DAFG", "Dayton Freight Lines", True, True, False),
	("ABFS", "ABF Freight", True, True, False),
	("PYLE", "A. Duie Pyle", True, True, False),
	("AACT", "AAA Cooper Transportation", True, True, False),
	("DPHE", "AAA Cooper Transportation", True, True, False),
	("MIDW", "AAA Cooper Transportation", True, True, False),
	("ABNE", "Aberdeen Express", True, True, False),
	("WLON", "Accurate Transport", True, True, False),
	("AGCE", "ACI Motor Freight, Inc", True, True, False),
	("ANCF", "Alliance Air Freight", True, True, False),
	("AXME", "Apex Motor Express", True, True, False),
	("AXRN", "Atcheson's Express", True, True, False),
	("AVRT", "Averitt Express", True, True, False),
	("BAKS", "Bakston Freight Systems, Inc", True, True, False),
	("BTVP", "Best Overnite Express", True, True, False),
	("CENF", "Central Freight Lines", True, True, False),
	("CTII", "Central Transport LLC", True, True, False),
	("CEVA", "CEVA Logistics", True, True, False),
	("CCYQ", "CrossCountry Freight Solutions", True, True, False),
	("CSAP", "CSA Transportation", True, True, False),
	("CMFC", "C&M Forwarding", True, True, False),
	("DAYR", "Day & Ross", True, True, False),
	("DYRR", "Day & Ross", True, True, False),
	("SDCR", "Day & Ross", True, True, False),
	("DYLT", "Daylight Transport LLC", True, True, False),
	("DCHA", "DC Logistics", True, True, False),
	("MTVL", "DC Logistics", True, True, False),
	("DLDS", "Diamond Line Delivery", True, True, False),
	("DHRN", "Dohrn Transfer Company", True, True, False),
	("SUON", "Dohrn Transfer Company", True, True, False),
	("UPPN", "Dohrn Transfer Company", True, True, False),
	("UPSD", "Dohrn Transfer Company", True, True, False),
	("DOLR", "Dot-Line Transportation", True, True, False),
	("DBDE", "Double D Express, Inc", True, True, False),
	("DUBL", "Dugan Truck Line", True, True, False),
	("EDXI", "EDI Express", True, True, False),
	("EAIS", "Expedite All", True, True, False),
	("FXFE", "FedEx Freight", True, True, False),
	("FXFC", "FedEx Freight", True, True, False),
	("FXFO", "FedEx Freight", True, True, False),
	("FXFR", "FedEx Freight", True, True, False),
	("FXNL", "FedEx Freight", True, True, False),
	("FXNR", "FedEx Freight", True, True, False),
	("FXFD", "FedEx Freight Direct Priority", True, True, False),
	("FXND", "FedEx Freight Direct Economy", True, True, False),
	("FFJM", "Flock Freight", True, True, False),
	("FLOK", "Flock Freight", True, True, False),
	("FLFI", "Fletes Mexico Carga Express", True, True, False),
	("FMXE", "Fletes Mexico Carga Express", True, True, False),
	("FWDN", "Forward Air, Inc", True, True, False),
	("FWDA", "Forward Air, Inc", True, True, False),
	("FWRA", "Forward Air, Inc", True, True, False),
	("FPAK", "FragilePAK", True, True, False),
	("CLNI", "Frontline Freight", True, True, False),
	("FCSY", "Frontline Freight", True, True, False),
	("FRZF", "Frozen Food Express", True, True, False),
	("GTJN", "Go2 Logistics", True, True, False),
	("HRCF", "Hercules Freight", True, True, False),
	("HMES", "Holland", True, True, False),
	("MAGN", "Magnum LTL", True, True, False),
	("MEFL", "Mainfreight", True, True, False),
	("MANI", "Manitoulin Transport", True, True, False),
	("NAFT", "N&M Transfer", True, True, False),
	("NPME", "New Penn", True, True, False),
	("OAKH", "Oak Harbor Freight Lines", True, True, False),
	("PITD", "Pitt Ohio", True, True, False),
	("RDFS", "R+L Carriers", True, True, False),
	("RETL", "Reddaway", True, True, False),
	("SEFL", "Southeastern Freight Lines", True, True, False),
	("TFF", "TForce Freight", True, True, False),
	("TFFA", "TForce Freight", True, True, False),
	("UPGF", "TForce Freight", True, True, False),
	("WARD", "Ward Transport", True, True, False),
	("CNWY", "XPO Logistics", True, True, False),
	("XPOL", "XPO Logistics", True, True, False),
	("RDWY", "YRC Freight", True, True, False),
	("YKWF", "YRC Freight", True, True, False),
]


EVA_ONBOARDED_CARRIERS: list[OnboardedCarrier] = [
	{
		"scac": scac,
		"name": name,
		"contract": contract,
		"dynamic": dynamic,
		"sandbox": sandbox,
	}
	for scac, name, contract, dynamic, sandbox in _MATRIX_ROWS
]

SCAC_DISPLAY_NAMES: dict[str, str] = {row["scac"]: row["name"] for row in EVA_ONBOARDED_CARRIERS}

# Seeded on by default for a new SMC3 connector. Existing rows keep their own Enabled flag.
DEFAULT_ENABLED_SCACS = {"SMCA", "ODFL", "SAIA", "EXLA", "DAFG", "ABFS", "PYLE"}


CONNECTOR_LABELS = {"SMC3", "SMC"}
SANDBOX_SCACS = {"SMCA"}
SANDBOX_EVA_ACCESS_ID = "SANDBOX-TEST-01"
SANDBOX_BILL_ACCOUNT = "1234567890"


def is_sandbox_scac(scac: str) -> bool:
	return str(scac or "").strip().upper() in SANDBOX_SCACS


def is_demo_display_name(name: str) -> bool:
	upper = str(name or "").strip().upper()
	return "DEMO CARRIER" in upper or upper == "SMC3 DEMO"


def carrier_display_name(scac: str, override: str | None = None, api_name: str | None = None) -> str:
	"""Return the clean primary name for an SMC3 network SCAC.

	Never returns the connector label "SMC3" or the sandbox demo label.
	Those belong in SOURCE / are remapped onto requested network SCACs.
	"""
	code = str(scac or "").strip().upper()
	for candidate in (override, api_name, SCAC_DISPLAY_NAMES.get(code), code):
		label = str(candidate or "").strip()
		if not label:
			continue
		if label.upper() in CONNECTOR_LABELS:
			continue
		if is_demo_display_name(label):
			continue
		return label
	return code if code and not is_sandbox_scac(code) else "Network Carrier"


def collapse_duplicate_brands(rows: list[dict]) -> list[dict]:
	"""Keep the first SCAC for each primary display name (AACT/DPHE/MIDW → AAA Cooper)."""
	seen: set[str] = set()
	unique: list[dict] = []
	for row in rows:
		scac = str(row.get("scac") or "").strip().upper()
		if not scac or is_sandbox_scac(scac):
			continue
		name = carrier_display_name(scac, row.get("carrier_label")).strip().upper()
		if not name or name in seen:
			continue
		seen.add(name)
		unique.append(row)
	return unique


def onboarded_row(scac: str) -> OnboardedCarrier | None:
	code = str(scac or "").strip().upper()
	return next((row for row in EVA_ONBOARDED_CARRIERS if row["scac"] == code), None)


def supports_contract_dynamic(scac: str) -> bool:
	row = onboarded_row(scac)
	if not row:
		return True
	return bool(row["contract"] or row["dynamic"])
