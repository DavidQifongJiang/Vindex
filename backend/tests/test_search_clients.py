import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import MagicMock, patch

from app.services import search_clients as clients


class SearchClientsTest(unittest.TestCase):
    def tearDown(self):
        clients.close_search_clients()

    def test_concurrent_acquisition_reuses_one_transport(self):
        with patch.object(clients, "QdrantClient") as factory:
            def acquire(_):
                with clients.semantic_client() as client:
                    return client
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(acquire, range(32)))
            factory.assert_called_once()
            self.assertTrue(all(client is results[0] for client in results))
            results[0].close.assert_not_called()
            clients.close_search_clients()
            results[0].close.assert_called_once()

    def test_shutdown_waits_for_inflight_request(self):
        entered, release = Event(), Event()
        with patch.object(clients, "QdrantClient", return_value=MagicMock()) as factory:
            def request():
                with clients.semantic_client():
                    entered.set()
                    release.wait(5)
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = executor.submit(request)
                self.assertTrue(entered.wait(5))
                shutdown = executor.submit(clients.close_search_clients)
                factory.return_value.close.assert_not_called()
                release.set()
                pending.result(5)
                shutdown.result(5)
            factory.return_value.close.assert_called_once()

    def test_failed_construction_can_retry(self):
        client = MagicMock()
        with patch.object(clients, "QdrantClient", side_effect=[RuntimeError(), client]):
            with self.assertRaises(RuntimeError):
                with clients.semantic_client():
                    pass
            with clients.semantic_client() as actual:
                self.assertIs(actual, client)

    def test_exception_releases_lease(self):
        with patch.object(clients, "QdrantClient") as factory:
            with self.assertRaises(ValueError):
                with clients.semantic_client():
                    raise ValueError()
            clients.close_search_clients()
            factory.return_value.close.assert_called_once()
