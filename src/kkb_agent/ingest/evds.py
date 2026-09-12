"""Small, synchronous client for the TCMB EVDS series endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from kkb_agent.config import Settings

DEFAULT_EVDS_BASE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis"
_EVDS_TIMEZONE = ZoneInfo("Europe/Istanbul")


class EVDSError(RuntimeError):
    """Base error for EVDS client failures."""


class EVDSConfigurationError(EVDSError):
    """The local EVDS client configuration is incomplete."""


class EVDSRequestError(EVDSError):
    """EVDS rejected a request or could not be reached."""


class EVDSResponseError(EVDSError):
    """EVDS returned a response that does not match the documented JSON shape."""


@dataclass(frozen=True)
class EVDSObservation:
    """One exact observation returned for a requested EVDS series."""

    period: date
    value: Decimal | None


class EVDSClient:
    """Fetch one EVDS series without persisting or transforming its observations."""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str = DEFAULT_EVDS_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EVDSClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def fetch_series(
        self,
        series_code: str,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[EVDSObservation, ...]:
        """Return observations for one series and inclusive date range."""

        code = series_code.strip()
        if not code:
            raise ValueError("series_code must not be empty")
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")

        api_key = self._settings.evds_api_key.get_secret_value().strip()
        if not api_key or api_key == "API_KEYINIZ":
            raise EVDSConfigurationError(
                "EVDS_API_KEY must be configured before requesting EVDS data."
            )

        encoded_code = quote(code, safe="._-")
        url = (
            f"{self._base_url}/series={encoded_code}"
            f"&startDate={start_date:%d-%m-%Y}"
            f"&endDate={end_date:%d-%m-%Y}&type=json"
        )
        try:
            response = self._client.get(url, headers={"key": api_key})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EVDSRequestError(f"EVDS returned HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise EVDSRequestError("EVDS request failed.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise EVDSResponseError("EVDS did not return valid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise EVDSResponseError("EVDS JSON response must contain an items list.")

        value_key = code.replace(".", "_")
        observations = []
        for index, item in enumerate(payload["items"]):
            if not isinstance(item, dict):
                raise EVDSResponseError(f"EVDS item {index} must be an object.")
            try:
                raw_period = item["Tarih"]
                raw_value = item[value_key]
            except KeyError as exc:
                raise EVDSResponseError(
                    f"EVDS item {index} is missing the requested series or Tarih field."
                ) from exc
            period = _parse_period(item, raw_period, index)

            if raw_value is None or str(raw_value).strip() in {"", "null"}:
                value = None
            else:
                try:
                    value = Decimal(str(raw_value))
                except InvalidOperation as exc:
                    raise EVDSResponseError(
                        f"EVDS item {index} contains a non-numeric series value."
                    ) from exc
            observations.append(EVDSObservation(period=period, value=value))
        return tuple(observations)


def _parse_period(item: dict, raw_period: object, index: int) -> date:
    text = str(raw_period).strip()
    for pattern in ("%d-%m-%Y", "%Y-%m"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass

    raw_unix = item.get("UNIXTIME")
    if isinstance(raw_unix, dict):
        raw_unix = raw_unix.get("$numberLong")
    try:
        return datetime.fromtimestamp(int(str(raw_unix)), tz=_EVDS_TIMEZONE).date()
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise EVDSResponseError(f"EVDS item {index} contains an invalid Tarih value.") from exc
