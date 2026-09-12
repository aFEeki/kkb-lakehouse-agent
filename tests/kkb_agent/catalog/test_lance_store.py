from kkb_agent.catalog.lance_store import LanceStore


def test_bootstrap_and_reopen(settings):
    store = LanceStore(settings.lancedb_path)
    assert store.check()
    assert settings.lancedb_path.is_dir()
    assert store.connect().list_tables().tables == []
    assert LanceStore(settings.lancedb_path).check()
