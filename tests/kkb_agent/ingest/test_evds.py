from datetime import date
from decimal import Decimal

import httpx
import pytest

from kkb_agent.config import Settings
from kkb_agent.ingest.evds import (
    EVDSClient,
    EVDSConfigurationError,
    EVDSRequestError,
    EVDSResponseError,
)


def make_settings(api_key: str = "test-key") -> Settings:
    return Settings(_env_file=None, evds_api_key=api_key)


def test_fetch_known_series_uses_header_and_parses_exact_value():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["key"] == "test-key"
        assert request.url.path.endswith(
            "/series=TP.DK.USD.S&startDate=02-01-2024&endDate=02-01-2024&type=json"
        )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "Tarih": "02-01-2024",
                        "TP_DK_USD_S": "29.49130000",
                        "UNIXTIME": {"$numberLong": "1704142800"},
                    }
                ]
            },
        )

    with EVDSClient(
        make_settings(),
        base_url="https://evds.example/service/evds",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.fetch_series(
            "TP.DK.USD.S",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
        )

    assert result[0].period == date(2024, 1, 2)
    assert result[0].value == Decimal("29.49130000")


def test_missing_observation_is_none_and_never_zero():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"items": [{"Tarih": "03-01-2024", "TP_DK_USD_S": None}]}
        )
    )
    with EVDSClient(make_settings(), transport=transport) as client:
        result = client.fetch_series(
            "TP.DK.USD.S",
            start_date=date(2024, 1, 3),
            end_date=date(2024, 1, 3),
        )

    assert result[0].value is None


def test_monthly_period_label_is_normalized_to_first_day():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"items": [{"Tarih": "2021-2", "TP_KFE_TR": "16.98000000"}]}
        )
    )
    with EVDSClient(make_settings(), transport=transport) as client:
        result = client.fetch_series(
            "TP.KFE.TR",
            start_date=date(2021, 1, 1),
            end_date=date(2021, 3, 31),
        )

    assert result[0].period == date(2021, 2, 1)


def test_unknown_period_label_uses_evds_unix_timestamp():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "Tarih": "unknown-label",
                        "TP_TEST": "1",
                        "UNIXTIME": {"$numberLong": "1609455600"},
                    }
                ]
            },
        )
    )
    with EVDSClient(make_settings(), transport=transport) as client:
        result = client.fetch_series(
            "TP.TEST",
            start_date=date(2021, 1, 1),
            end_date=date(2021, 1, 1),
        )

    assert result[0].period == date(2021, 1, 1)


def test_missing_api_key_fails_before_network():
    transport = httpx.MockTransport(
        lambda _request: pytest.fail("network must not be called without a key")
    )
    with EVDSClient(make_settings(""), transport=transport) as client:
        with pytest.raises(EVDSConfigurationError, match="EVDS_API_KEY"):
            client.fetch_series(
                "TP.DK.USD.S",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 2),
            )


def test_http_error_does_not_expose_key_or_response_body():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(403, text="secret diagnostic response")
    )
    with EVDSClient(make_settings("private-key"), transport=transport) as client:
        with pytest.raises(EVDSRequestError) as error:
            client.fetch_series(
                "TP.DK.USD.S",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 2),
            )

    assert "HTTP 403" in str(error.value)
    assert "private-key" not in str(error.value)
    assert "secret diagnostic response" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": "not-a-list"},
        {"items": [{}]},
        {"items": [{"Tarih": "invalid", "TP_DK_USD_S": "29.4382"}]},
        {"items": [{"Tarih": "02-01-2024", "TP_DK_USD_S": "not-a-number"}]},
    ],
)
def test_invalid_response_is_rejected(payload):
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    with EVDSClient(make_settings(), transport=transport) as client:
        with pytest.raises(EVDSResponseError):
            client.fetch_series(
                "TP.DK.USD.S",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 2),
            )


def test_rejects_reversed_date_range():
    with EVDSClient(make_settings()) as client:
        with pytest.raises(ValueError, match="end_date"):
            client.fetch_series(
                "TP.DK.USD.S",
                start_date=date(2024, 1, 3),
                end_date=date(2024, 1, 2),
            )
