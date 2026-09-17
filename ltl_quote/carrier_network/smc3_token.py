# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Token Manager — client-credentials retrieval, in-memory cache, 401 retry."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import frappe
import requests
from frappe.utils import cint

DEFAULT_TOKEN_URL = "https://api.smc3.com/TokenRetrieval"
REQUEST_TIMEOUT = 20
REFRESH_SKEW_SECONDS = 60
CONF_CLIENT_ID = "smc3_client_id"
CONF_CLIENT_SECRET = "smc3_client_secret"
ENV_CLIENT_ID = "SMC3_CLIENT_ID"
ENV_CLIENT_SECRET = "SMC3_CLIENT_SECRET"

AUTH_USER_MESSAGE = "SMC3 session expired. Fetch Rates again to reconnect."

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_LOCK = threading.RLock()
_FETCH_LOCK = threading.RLock()
_MANAGERS: dict[str, "SMC3TokenManager"] = {}
_MANAGER_LOCK = threading.Lock()


class SMC3AuthError(Exception):
	"""Raised when a Bearer token cannot be obtained."""


class SMC3TokenManager:
	"""Process-wide singleton that fetches and caches SMC3 access tokens."""

	def __init__(self, carrier_doc=None, config: dict | None = None):
		self.cache_key = str(getattr(carrier_doc, "name", None) or "SMC3")
		self.bind(carrier_doc, config)

	@classmethod
	def get(cls, carrier_doc=None, config: dict | None = None) -> "SMC3TokenManager":
		key = str(getattr(carrier_doc, "name", None) or "SMC3")
		with _MANAGER_LOCK:
			manager = _MANAGERS.get(key)
			if manager is None:
				manager = cls(carrier_doc, config)
				_MANAGERS[key] = manager
			else:
				manager.bind(carrier_doc, config)
			return manager

	def bind(self, carrier_doc=None, config: dict | None = None) -> None:
		self.carrier_doc = carrier_doc
		self.config = config or {}
		self.cache_key = str(getattr(carrier_doc, "name", None) or self.cache_key or "SMC3")

	def get_token(self, force_refresh: bool = False) -> str:
		if not force_refresh:
			cached = self._cached()
			if cached:
				return cached
		with _FETCH_LOCK:
			if not force_refresh:
				cached = self._cached()
				if cached:
					return cached
			token, expires_at = self._fetch_access_token()
			with _CACHE_LOCK:
				_TOKEN_CACHE[self.cache_key] = (token, expires_at)
			return token

	def clear(self) -> None:
		with _CACHE_LOCK:
			_TOKEN_CACHE.pop(self.cache_key, None)

	def request(self, method: str, url: str, retry_auth: bool = True, **kwargs):
		"""Send an SMC3 HTTP call with a fresh Bearer token and one 401 retry."""
		headers = dict(kwargs.pop("headers", None) or {})
		timeout = kwargs.pop("timeout", REQUEST_TIMEOUT)
		headers["Authorization"] = f"Bearer {self.get_token()}"
		response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
		if retry_auth and is_invalid_access_token(response):
			self.clear()
			headers["Authorization"] = f"Bearer {self.get_token(force_refresh=True)}"
			response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
		return response

	def _cached(self) -> str | None:
		with _CACHE_LOCK:
			entry = _TOKEN_CACHE.get(self.cache_key)
		if not entry:
			return None
		token, expires_at = entry
		if expires_at > time.time() + REFRESH_SKEW_SECONDS:
			return token
		return None

	def _fetch_access_token(self) -> tuple[str, float]:
		client_id, client_secret = self._credentials()
		if not client_id or not client_secret:
			raise SMC3AuthError(
				"SMC3 CLIENT_ID and CLIENT_SECRET are missing. "
				"Set SMC3_CLIENT_ID / SMC3_CLIENT_SECRET or site_config smc3_client_id / smc3_client_secret."
			)
		token_url = str(self.config.get("token_url") or DEFAULT_TOKEN_URL).strip() or DEFAULT_TOKEN_URL
		try:
			response = requests.post(
				token_url,
				data={
					"grant_type": "client_credentials",
					"client_id": client_id,
					"client_secret": client_secret,
				},
				headers={
					"Content-Type": "application/x-www-form-urlencoded",
					"Accept": "application/json",
				},
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as exc:
			raise SMC3AuthError(f"SMC3 token request failed: {exc}") from exc

		if response.status_code != 200:
			raise SMC3AuthError(f"SMC3 token request failed: HTTP {response.status_code}")

		try:
			payload = response.json() if response.content else {}
		except ValueError as exc:
			raise SMC3AuthError("SMC3 token endpoint returned non-JSON") from exc

		if not isinstance(payload, dict):
			raise SMC3AuthError("SMC3 token endpoint returned an unexpected payload")

		token = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
		if not token:
			raise SMC3AuthError("SMC3 token response missing access_token")

		expires_in = max(cint(payload.get("expires_in") or payload.get("expiresIn") or 3600), 60)
		return token, time.time() + expires_in

	def _credentials(self) -> tuple[str, str]:
		client_id = _clean(
			os.environ.get(ENV_CLIENT_ID)
			or _conf_value(CONF_CLIENT_ID)
			or _password_or_plain(self.carrier_doc, "api_key")
		)
		client_secret = _clean(
			os.environ.get(ENV_CLIENT_SECRET)
			or _conf_value(CONF_CLIENT_SECRET)
			or _password_or_plain(self.carrier_doc, "api_secret")
		)
		if client_id.lower().startswith("bearer "):
			client_id = client_id[7:].strip()
		# A leftover pricing JWT in api_key is not a client id.
		if _looks_like_jwt(client_id):
			client_id = _clean(os.environ.get(ENV_CLIENT_ID) or _conf_value(CONF_CLIENT_ID))
		return client_id, client_secret


class SMC3TokenService:
	"""Adapter-facing wrapper around the process-wide SMC3TokenManager singleton."""

	def __init__(self, carrier_doc, config: dict | None = None):
		self._manager = SMC3TokenManager.get(carrier_doc, config)

	def get_token(self, force_refresh: bool = False) -> str:
		return self._manager.get_token(force_refresh=force_refresh)

	def clear(self) -> None:
		self._manager.clear()

	def request(self, method: str, url: str, retry_auth: bool = True, **kwargs):
		return self._manager.request(method, url, retry_auth=retry_auth, **kwargs)


def persist_smc3_client_credentials(client_id: str, client_secret: str) -> None:
	"""Save SMC3 OAuth client credentials to site_config and the SMC3 carrier record."""
	client_id = _clean(client_id)
	client_secret = _clean(client_secret)
	if not client_id or not client_secret:
		frappe.throw("SMC3 CLIENT_ID and CLIENT_SECRET are required.")

	from frappe.installer import update_site_config

	update_site_config(CONF_CLIENT_ID, client_id)
	update_site_config(CONF_CLIENT_SECRET, client_secret)
	frappe.conf[CONF_CLIENT_ID] = client_id
	frappe.conf[CONF_CLIENT_SECRET] = client_secret

	if frappe.db.exists("LTL Carrier", "SMC3"):
		doc = frappe.get_doc("LTL Carrier", "SMC3")
		doc.api_key = client_id
		doc.api_secret = client_secret
		doc.auth_type = "OAuth2"
		notes = {}
		raw = (doc.notes or "").strip()
		if raw.startswith("{"):
			try:
				parsed = frappe.parse_json(raw)
				if isinstance(parsed, dict):
					notes = parsed
			except Exception:
				notes = {}
		elif raw:
			notes["_notes"] = raw
		notes["token_url"] = notes.get("token_url") or DEFAULT_TOKEN_URL
		doc.notes = frappe.as_json(notes)
		doc.save(ignore_permissions=True)

	with _MANAGER_LOCK:
		_MANAGERS.clear()
	with _CACHE_LOCK:
		_TOKEN_CACHE.clear()


def is_invalid_access_token(response) -> bool:
	"""True when SMC3 rejected the Bearer token (HTTP 401 or invalid/expired token body)."""
	if response is None:
		return False
	status = getattr(response, "status_code", None)
	text = (getattr(response, "text", None) or "").lower()
	if status == 401:
		return True
	if is_auth_error_text(text):
		return True
	try:
		payload = response.json() if getattr(response, "content", None) else {}
	except ValueError:
		return False
	return _payload_is_auth_failure(payload)


def is_auth_error_text(value) -> bool:
	raw = str(value or "").lower()
	return any(
		token in raw
		for token in (
			"invalid access token",
			"invalid_token",
			"expired token",
			"access token expired",
			"carrier authentication expired",
			"session expired",
			"unauthorized",
			"invalid_client",
		)
	)


def is_tforce_connector_text(carrier_name=None, error_text=None) -> bool:
	blob = f"{carrier_name or ''} {error_text or ''}".upper()
	return any(token in blob for token in ("TFORCE", "TFFA"))


TFORCE_AUTH_USER_MESSAGE = "TForce Freight: credentials missing or expired"


def should_hide_auth_error(carrier_name=None, error_text=None) -> bool:
	"""UI/API used to drop all 401s; TForce auth must still surface on get_ltl_rates."""
	if not is_auth_error_text(error_text):
		return False
	return not is_tforce_connector_text(carrier_name, error_text)


def _payload_is_auth_failure(payload: Any) -> bool:
	if not isinstance(payload, dict):
		return False
	status = payload.get("messageStatus") if isinstance(payload.get("messageStatus"), dict) else {}
	blob = " ".join(
		str(part or "")
		for part in (
			payload.get("error"),
			payload.get("error_description"),
			payload.get("message"),
			status.get("message"),
			status.get("code"),
		)
	)
	return is_auth_error_text(blob)


def _conf_value(key: str) -> str:
	try:
		return str(frappe.conf.get(key) or "")
	except Exception:
		return ""


def _password_or_plain(carrier_doc, field: str) -> str:
	if not carrier_doc:
		return ""
	value = ""
	if hasattr(carrier_doc, "get_password"):
		value = carrier_doc.get_password(field, raise_exception=False) or ""
	if value:
		return str(value)
	plain = carrier_doc.get(field) or ""
	if plain and hasattr(carrier_doc, "is_dummy_password") and carrier_doc.is_dummy_password(plain):
		return ""
	return str(plain or "")


def _looks_like_jwt(value: str) -> bool:
	parts = str(value or "").split(".")
	return len(parts) == 3 and all(parts)


def _clean(value) -> str:
	return str(value or "").strip()
