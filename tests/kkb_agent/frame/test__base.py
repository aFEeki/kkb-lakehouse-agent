import pytest
from pydantic import ValidationError

from kkb_agent.frame import MetadataEntry


@pytest.mark.parametrize("value", [{"mutable": 1}, [1], float("nan")])
def test_metadata_rejects_mutable_or_nonfinite_values(value):
    with pytest.raises(ValidationError):
        MetadataEntry(key="extra", value=value)


def test_metadata_json_is_detached():
    entry = MetadataEntry(key="extra", value="original")
    payload = entry.model_dump()
    payload["value"] = "changed"
    assert entry.value == "original"
