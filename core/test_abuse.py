from datetime import timedelta

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .abuse import consume, prune
from .models import AbuseCounter


class DistributedAbuseTests(TestCase):
    @override_settings(SECRET_KEY="test-secret-key-for-abuse-counters")
    def test_counter_is_hash_only_and_windowed(self):
        request = RequestFactory().post("/")
        request.META["REMOTE_ADDR"] = "198.51.100.8"
        self.assertTrue(consume(request, scope="test", limit=2, window_seconds=60))
        self.assertTrue(consume(request, scope="test", limit=2, window_seconds=60))
        self.assertFalse(consume(request, scope="test", limit=2, window_seconds=60))
        row = AbuseCounter.objects.get(scope="test")
        self.assertNotIn("198.51.100.8", row.identifier_hash)
        self.assertEqual(row.count, 2)

    def test_prune_removes_only_old_windows(self):
        old = timezone.now() - timedelta(days=2)
        AbuseCounter.objects.create(scope="old", identifier_hash="a" * 64, window_started_at=old)
        AbuseCounter.objects.create(scope="new", identifier_hash="b" * 64, window_started_at=timezone.now())
        self.assertEqual(prune(before=timezone.now() - timedelta(days=1)), 1)
        self.assertFalse(AbuseCounter.objects.filter(scope="old").exists())
        self.assertTrue(AbuseCounter.objects.filter(scope="new").exists())

    @override_settings(ABUSE_TRUSTED_PROXY_IPS=())
    def test_forwarded_header_is_ignored_without_trusted_proxy(self):
        request = RequestFactory().post("/")
        request.META["REMOTE_ADDR"] = "192.0.2.10"
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.20"
        self.assertTrue(consume(request, scope="proxy", limit=1, window_seconds=60))
        row = AbuseCounter.objects.get(scope="proxy")
        self.assertNotEqual(row.identifier_hash, "198.51.100.20")

    @override_settings(ABUSE_TRUSTED_PROXY_IPS=("192.0.2.10",))
    def test_forwarded_header_is_used_only_for_trusted_proxy(self):
        request = RequestFactory().post("/")
        request.META["REMOTE_ADDR"] = "192.0.2.10"
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.20"
        self.assertTrue(consume(request, scope="trusted-proxy", limit=1, window_seconds=60))
