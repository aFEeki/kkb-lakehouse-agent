from kkb_agent.ingest.base import SourceAdapter


class DummySource:
    def discover(self):
        return ["dummy:one"]

    def fetch(self, reference, destination):
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "raw.txt"
        path.write_text(reference)
        return path

    def parse(self, raw_path):
        return [{"source_reference": raw_path.read_text()}]


def test_adapter_contract(tmp_path):
    adapter = DummySource()
    assert isinstance(adapter, SourceAdapter)
    reference = next(iter(adapter.discover()))
    path = adapter.fetch(reference, tmp_path / "bronze")
    assert list(adapter.parse(path)) == [{"source_reference": "dummy:one"}]
    assert not isinstance(object(), SourceAdapter)
