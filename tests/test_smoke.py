def test_imports():
    import scraper.run, scraper.merge, scraper.notify, scraper.pipeline  # noqa: F401
    from scraper.adapters import REGISTRY
    assert "workday" in REGISTRY and "generic" in REGISTRY
