"""Tests for the vendored cloudscraper copy in vendor/cloudscraper.

main.py puts vendor/ first on sys.path, so the vendored copy is the one that runs
in production. CI installs vendor/cloudscraper-3.0.0.zip instead, which means a
plain ``import cloudscraper`` here would test the zip, not the vendored code.
Load the vendored package explicitly under its own module name.
"""

import importlib.util
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "cloudscraper"
MODULE_NAME = "_vendored_cloudscraper"


def _load_vendored_cloudscraper():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        VENDOR_DIR / "__init__.py",
        submodule_search_locations=[str(VENDOR_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


class TestTlsCipherRotation:
    def setup_method(self):
        self.cloudscraper = _load_vendored_cloudscraper()
        assert Path(self.cloudscraper.__file__).resolve().parent == VENDOR_DIR

    def test_rotation_closes_replaced_adapter(self):
        """The replaced HTTPS adapter must be closed, or its pooled keep-alive
        sockets stay open for the life of the process."""
        scraper = self.cloudscraper.create_scraper(min_request_interval=0)
        old_adapter = scraper.adapters["https://"]
        # Creating a pool does not open a connection, so this stays offline.
        old_adapter.poolmanager.connection_from_url("https://www.opensubtitles.org/")
        assert len(old_adapter.poolmanager.pools) == 1

        scraper._rotate_tls_cipher_suite()

        new_adapter = scraper.adapters["https://"]
        assert new_adapter is not old_adapter
        assert isinstance(new_adapter, self.cloudscraper.CipherSuiteAdapter)
        assert len(old_adapter.poolmanager.pools) == 0

    def test_rotation_keeps_new_adapter_usable(self):
        scraper = self.cloudscraper.create_scraper(min_request_interval=0)
        scraper._rotate_tls_cipher_suite()
        new_adapter = scraper.adapters["https://"]

        new_adapter.poolmanager.connection_from_url("https://www.opensubtitles.org/")
        assert len(new_adapter.poolmanager.pools) == 1
        assert new_adapter.ssl_context.get_ciphers()
