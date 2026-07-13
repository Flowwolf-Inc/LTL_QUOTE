import json
import xml.etree.ElementTree as ET
import frappe
import requests
from frappe.utils import cint, flt, getdate, nowdate

from ltl_quote.carrier_network.accessorials import (
    arcbest_accessorial_params,
    build_accessorial_items_from_payload,
)
from ltl_quote.carrier_network.adapters.base import (
    AccessorialItem,
    BaseCarrierAdapter,
    CarrierRateQuote,
    ShipmentRequest,
)

DEFAULT_BASE_URL = "https://www.abfs.com/xml/aquotexml.asp"
DEFAULT_API_ID = "H0TTC3W3"
BOL_URL = "https://www.abfs.com/xml/bolxml.asp"
SANDBOX_QUOTE_ID = "1234567890"


class ArcBestCarrierAdapter(BaseCarrierAdapter):
    """Real-time production adapter for ArcBest (ABF Freight) XML rate engine."""

    def __init__(self, carrier_doc=None):
        super().__init__(carrier_doc)

        self.carrier_doc = carrier_doc or self.carrier
        if not self.carrier_doc and getattr(self.carrier, "name", None):
            self.carrier_doc = self.carrier
        elif not self.carrier_doc:
            self.carrier_doc = self._load_carrier_doc("ARCB")

        if self.carrier_doc:
            self.carrier = self.carrier_doc

        self.base_url = (self.carrier_doc.get("api_base_url") if self.carrier_doc else None) or DEFAULT_BASE_URL
        self.base_url = self.base_url.rstrip("/")
        self.api_id = self._get_api_id()

    @staticmethod
    def _load_carrier_doc(preferred_name: str):
        if frappe.db.exists("LTL Carrier", preferred_name):
            return frappe.get_doc("LTL Carrier", preferred_name)
        return None

    def _get_api_id(self) -> str:
        if self.carrier_doc and hasattr(self.carrier_doc, "get_password"):
            api_id = self.carrier_doc.get_password("api_key", raise_exception=False) or ""
            if api_id:
                return api_id
            plain_key = self.carrier_doc.get("api_key")
            if plain_key and not self.carrier_doc.is_dummy_password(plain_key):
                return plain_key
        return DEFAULT_API_ID

    def get_rates(self, request: ShipmentRequest) -> CarrierRateQuote:
        """Map ShipmentRequest -> ArcBest GET params -> parse ABF XML -> CarrierRateQuote."""
        params = {}
        try:
            settings = frappe.get_single("LTL Platform Settings")
            timeout = cint(settings.rate_request_timeout_seconds) or 30
            params = self._build_rate_params(request)

            response = requests.get(self.base_url, params=params, timeout=timeout)

            if response.status_code != 200:
                return self._error_quote(f"ArcBest API error: HTTP {response.status_code}")

            quote = self._parse_rate_xml(response.content, response.text, params)
            return quote

        except requests.exceptions.RequestException as e:
            frappe.log_error(message=str(e), title="ArcBest API Connection Error")
            return self._error_quote(f"ArcBest connection error: {e}")

        except Exception as e:
            frappe.log_error(
                message=f"Failed to bind live pricing object: {e}",
                title="Carrier Adapter Exception",
            )
            return self._error_quote(f"ArcBest adapter error: {e}")

    def book_shipment(self, quote_data: dict) -> dict:
        """Create ArcBest BOL via bolxml.asp and return a normalized booking result."""
        quote_data = quote_data or {}
        settings = frappe.get_single("LTL Platform Settings")
        timeout = cint(settings.rate_request_timeout_seconds) or 15
        quote_id = self._resolve_bol_quote_id(quote_data)
        is_test = bool(quote_data.get("is_test"))

        params = self._build_bol_params(quote_data, quote_id=quote_id, is_test=is_test)

        try:
            response = requests.get(BOL_URL, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            frappe.log_error(message=str(e), title="ArcBest BOL Connection Error")
            frappe.throw(f"ArcBest BOL request failed: {e}")

        if response.status_code != 200:
            frappe.throw(f"ArcBest BOL API error: HTTP {response.status_code}")

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as pe:
            frappe.log_error(
                message=f"{pe}\n\nRaw: {response.text[:2000]}",
                title="ArcBest BOL XML Parse Failure",
            )
            frappe.throw(f"ArcBest BOL XML parse error: {pe}")

        num_errors = cint(self._xml_text(root, "NUMERRORS") or "0")
        if num_errors > 0:
            error_msg = self._extract_error_message(root)
            frappe.throw(f"ArcBest API Rejected: {error_msg}")

        bol_number = self._xml_text(root, "BOLNUMBER") or str(
            quote_data.get("carrier_quote_id") or quote_id or ""
        )
        pro_number = self._xml_text(root, "PRONUMBER") or "Auto-Assigned"
        bol_document_url = self._xml_text(root, "DOCUMENT")

        return {
            "status": "booked",
            "bol_number": bol_number,
            "pro_number": pro_number,
            "carrier_confirmation": bol_number,
            "bol_document_url": bol_document_url,
            "document_binary": "",
        }

    def _build_bol_params(self, quote_data: dict, quote_id: str, is_test: bool) -> dict:
        ship_date = getdate(quote_data.get("pickup_date") or nowdate())
        return {
            "ID": self.api_id,
            "TEST": "Y" if is_test else "N",
            "RequesterType": "1",
            "PayTerms": "P",
            "RequesterName": quote_data.get("contact_name")
            or quote_data.get("origin_contact_name")
            or "JOHN BLACK",
            "RequesterPhone": quote_data.get("contact_phone")
            or quote_data.get("origin_contact_phone")
            or "5555555555",
            "ShipName": quote_data.get("shipper_name")
            or quote_data.get("shipper_company_name")
            or "XYZ Corp",
            "ShipAddress": quote_data.get("shipper_address") or "123 MAIN",
            "ShipCity": quote_data.get("origin_city") or "Dyer",
            "ShipState": quote_data.get("origin_state") or "AR",
            "ShipZip": str(quote_data.get("origin_zip") or "72935"),
            "ConsName": quote_data.get("consignee_name")
            or quote_data.get("consignee_company_name")
            or "ABC Corp",
            "ConsAddress": quote_data.get("consignee_address") or "321 Elm",
            "ConsCity": quote_data.get("destination_city") or "LAWRENCE",
            "ConsState": quote_data.get("destination_state") or "KS",
            "ConsZip": str(quote_data.get("destination_zip") or "66044"),
            "ShipDate": ship_date.strftime("%m/%d/%Y"),
            "HN1": str(cint(quote_data.get("pieces") or 100)),
            "HT1": "PLT",
            "WT1": str(cint(quote_data.get("total_weight") or 1000)),
            "CL1": str(quote_data.get("freight_class") or "65"),
            "Desc1": quote_data.get("commodity_description") or "MISC AUTO PARTS",
            "QuoteID": quote_id,
        }

    def _resolve_bol_quote_id(self, quote_data: dict) -> str:
        """Normalize QuoteID for ArcBest BOL; prefer live quote-request rate lines."""
        incoming = str(quote_data.get("carrier_quote_id") or "").strip()
        quote_request_name = quote_data.get("quote_request")
        arcb_rows = []

        if quote_request_name and frappe.db.exists("LTL Quote Request", quote_request_name):
            doc = frappe.get_doc("LTL Quote Request", quote_request_name)
            arcb_rows = [
                row for row in (doc.carrier_quotes or []) if row.carrier in ("ARCB", "ARCBEST")
            ]

        if arcb_rows:
            incoming_norm = self._normalize_quote_id(incoming) if incoming else ""
            for row in arcb_rows:
                row_norm = self._normalize_quote_id(row.carrier_quote_id)
                if not row_norm:
                    continue
                if incoming and (
                    incoming == row.carrier_quote_id
                    or incoming_norm == row_norm
                    or incoming.replace("ABF-", "").replace("abf-", "").strip()
                    == row_norm.lstrip("0")
                ):
                    return row_norm
            if arcb_rows[0].carrier_quote_id:
                return self._normalize_quote_id(arcb_rows[0].carrier_quote_id)

        if not incoming:
            return ""

        normalized = self._normalize_quote_id(incoming)
        if normalized == SANDBOX_QUOTE_ID:
            return normalized

        clean = incoming.upper().replace("ABF-", "").strip()
        if clean in ("3322EF35",) or "3322EF35" in clean:
            return ""

        return normalized

    @staticmethod
    def _normalize_quote_id(carrier_quote_id) -> str:
        """Strip ABF- prefix and pad to 10 chars for ArcBest Code 154 validation."""
        raw = str(carrier_quote_id or "").strip()
        if raw.upper().startswith("ABF-"):
            raw = raw[4:].strip()
        if len(raw) < 10 and raw:
            return raw.zfill(10)
        return raw

    @staticmethod
    def _find_xml_node(root, tag_name: str):
        tag_upper = tag_name.upper()
        node = root.find(tag_name)
        if node is not None:
            return node
        for variant in (tag_name.upper(), tag_name.lower(), tag_name.title()):
            node = root.find(variant)
            if node is not None:
                return node
        node = root.find(f".//{tag_name}")
        if node is not None:
            return node
        for elem in root.iter():
            local_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_tag.upper() == tag_upper:
                return elem
        return None

    @classmethod
    def _xml_text(cls, root, tag_name: str, default: str = "") -> str:
        node = cls._find_xml_node(root, tag_name)
        if node is not None and node.text:
            return node.text.strip()
        return default

    @classmethod
    def _extract_error_message(cls, root) -> str:
        error_elements = root.findall(".//ERROR")
        messages: list[str] = []
        for err in error_elements:
            code = cls._xml_text(err, "ERRORCODE")
            msg = cls._xml_text(err, "ERRORMESSAGE")
            if msg:
                messages.append(f"Code {code}: {msg}" if code else msg)
        if messages:
            return " | ".join(messages)
        error_node = cls._find_xml_node(root, "ERRORMESSAGE")
        if error_node is not None and error_node.text:
            return error_node.text.strip()
        return "Validation Error"

    def get_tracking(self, pro_number: str) -> list[dict]:
        frappe.log_error(
            message=f"Tracking not implemented for ArcBest PRO {pro_number}",
            title="ArcBest Tracking Not Implemented",
        )
        return []

    def _carrier_name(self) -> str:
        return getattr(self.carrier, "carrier_name", None) or "ArcBest Freight"

    def _error_quote(self, message: str) -> CarrierRateQuote:
        return CarrierRateQuote(
            carrier_code=self.carrier_code or "ARCB",
            carrier_name=self._carrier_name(),
            total_charge=0,
            transit_days=0,
            error=message,
        )

    def _read_raw_request_json(self) -> dict:
        """Read inbound API JSON when controller fields are missing (e.g. Postman body)."""
        try:
            if not getattr(frappe, "request", None):
                return {}

            if getattr(frappe.request, "json", None):
                raw = frappe.request.json
                return raw if isinstance(raw, dict) else {}

            if frappe.request.data:
                raw = json.loads(frappe.request.data.decode("utf-8"))
                return raw if isinstance(raw, dict) else {}
        except Exception:
            pass
        return {}

    def _extract_request_fields(self, request) -> dict:
        """Read fields with dynamic fallback to raw JSON if ShipmentRequest values are missing."""
        raw_json = self._read_raw_request_json()

        def city_state(value, raw_key: str, default: str = "") -> str:
            return (value or raw_json.get(raw_key) or default).strip()

        if isinstance(request, ShipmentRequest):
            return {
                "origin_zip": request.origin_zip,
                "origin_city": city_state(request.origin_city, "origin_city", "DAYTON"),
                "origin_state": city_state(request.origin_state, "origin_state", "OH"),
                "origin_country": "US",
                "destination_zip": request.destination_zip,
                "destination_city": city_state(request.destination_city, "destination_city", "CHICAGO"),
                "destination_state": city_state(request.destination_state, "destination_state", "IL"),
                "destination_country": "US",
                "total_weight": request.total_weight,
                "freight_class": request.freight_class,
                "pieces": request.pieces,
                "unit_type": "PLT",
                "accessorials": request.accessorials or [],
            }

        items = request.get("items") or [{}]
        first_item = items[0] if isinstance(items, list) and items else {}

        return {
            "origin_zip": request.get("origin_zip"),
            "origin_city": city_state(request.get("origin_city"), "origin_city", "DAYTON"),
            "origin_state": city_state(request.get("origin_state"), "origin_state", "OH"),
            "origin_country": request.get("origin_country") or "US",
            "destination_zip": request.get("destination_zip"),
            "destination_city": city_state(request.get("destination_city"), "destination_city", "CHICAGO"),
            "destination_state": city_state(request.get("destination_state"), "destination_state", "IL"),
            "destination_country": request.get("destination_country") or "US",
            "total_weight": request.get("total_weight") or first_item.get("weight") or 400,
            "freight_class": request.get("freight_class") or first_item.get("classification") or first_item.get("freight_class") or "70",
            "pieces": request.get("pieces") or request.get("total_qty") or first_item.get("qty") or 1,
            "unit_type": first_item.get("unit_type") or "PLT",
            "accessorials": request.get("accessorials") or request.get("accessorial_codes") or [],
        }

    def _build_rate_params(self, request) -> dict:
        fields = self._extract_request_fields(request)
        ship_date = getdate(nowdate())
        accessorial_items = self._coerce_accessorial_items(fields.get("accessorials") or [])

        origin_zip = str(fields.get("origin_zip") or "")
        destination_zip = str(fields.get("destination_zip") or "")
        weight = fields.get("total_weight") or 400
        freight_class = fields.get("freight_class") or "50.0"
        pieces = fields.get("pieces") or 1

        params = {
            "DL": "2",
            "ID": self.api_id,
            "ShipCity": fields.get("origin_city") or "",
            "ShipState": fields.get("origin_state") or "",
            "ShipZip": origin_zip,
            "ShipCountry": fields.get("origin_country") or "US",
            "ConsCity": fields.get("destination_city") or "",
            "ConsState": fields.get("destination_state") or "",
            "ConsZip": destination_zip,
            "ConsCountry": fields.get("destination_country") or "US",
            "Wgt1": cint(self._clean_numeric(weight, 400)),
            "Class1": flt(self._clean_numeric(freight_class, "50.0")),
            "UnitNo1": cint(self._clean_numeric(pieces, 1)),
            "UnitType1": fields.get("unit_type") or "PLT",
            "ShipAff": "Y",
            "ShipMonth": f"{ship_date.month:02d}",
            "ShipDay": f"{ship_date.day:02d}",
            "ShipYear": str(ship_date.year),
        }
        params.update(arcbest_accessorial_params(accessorial_items, self.carrier_doc))

        return params

    @staticmethod
    def _coerce_accessorial_items(accessorials) -> list[AccessorialItem]:
        if not accessorials:
            return []
        if isinstance(accessorials[0], AccessorialItem):
            return accessorials
        if isinstance(accessorials[0], dict):
            return build_accessorial_items_from_payload(accessorials)
        return build_accessorial_items_from_payload([{"code": code, "quantity": 1} for code in accessorials])

    def _parse_rate_xml(self, content: bytes, raw_text: str, params: dict | None = None) -> CarrierRateQuote:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as pe:
            frappe.log_error(message=str(pe), title="ArcBest XML Tree Parsing Failure")
            return CarrierRateQuote(
                carrier_code=self.carrier_code,
                carrier_name=self._carrier_name(),
                total_charge=0,
                transit_days=0,
                error=f"ArcBest XML parse error: {pe}",
            )

        num_errors = cint(root.findtext("NUMERRORS", "0"))
        if num_errors > 0:
            error_elements = root.findall(".//ERROR")
            error_msg = ""
            for err in error_elements:
                code = err.findtext("ERRORCODE", "")
                msg = err.findtext("ERRORMESSAGE", "")
                error_msg += f"Code {code}: {msg} | "

            if not error_msg:
                error_msg = root.findtext(".//ERRORMESSAGE", "Unknown Location Validation Error")

            frappe.log_error(
                message=f"Params Sent: {params} \n\nResponse: {error_msg}\n\nRaw XML: {raw_text}",
                title="ArcBest API Error",
            )
            return CarrierRateQuote(
                carrier_code=self.carrier_code,
                carrier_name=self._carrier_name(),
                total_charge=0,
                transit_days=0,
                error=f"ArcBest API error: {error_msg.strip(' |')}",
            )

        quote_id = root.findtext("QUOTEID") or ""
        total_charge = flt(root.findtext("CHARGE"))
        transit_str = root.findtext("ADVERTISEDTRANSIT") or "0"
        transit_days = cint("".join(filter(str.isdigit, transit_str))) or 1
        delivery_date = root.findtext("ADVERTISEDDUEDATE")

        fuel_surcharge = 0.0
        fuel_node = root.find(".//FUELSURCHARGE")
        if fuel_node is not None and fuel_node.text:
            fuel_surcharge = flt(fuel_node.text)

        linehaul = total_charge - fuel_surcharge

        return CarrierRateQuote(
            carrier_code=self.carrier_code or "ARCB",
            carrier_name=self._carrier_name(),
            total_charge=total_charge,
            transit_days=transit_days,
            linehaul_charge=linehaul,
            fuel_surcharge=fuel_surcharge,
            accessorial_charge=0.0,
            currency="USD",
            carrier_quote_id=f"ABF-{quote_id}" if quote_id else "",
            service_level=root.findtext("SERVICETYPE") or "Standard LTL",
            reliability_score=float(getattr(self.carrier, "reliability_score", None) or 90),
            raw_response={
                "quote_id": quote_id,
                "delivery_date": delivery_date,
                "transit_text": transit_str,
                "xml": raw_text,
            },
        )

    @staticmethod
    def _clean_numeric(value, default=0):
        return str(value if value is not None else default).replace(",", "")

    @staticmethod
    def _clean_int(value, default=0) -> int:
        cleaned = ArcBestCarrierAdapter._clean_numeric(value, default)
        return int(float(cleaned))