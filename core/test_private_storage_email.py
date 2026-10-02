from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.checks import Error
from django.core.exceptions import SuspiciousOperation
from django.test import SimpleTestCase, override_settings

from payments.storage import get_voucher_storage
from tickets.checks import private_storage_and_email_configuration
from tickets.storage import PrivateTicketFileSystemStorage, get_ticket_storage


class PrivateStorageAndEmailConfigurationTests(SimpleTestCase):
    def test_filesystem_storage_is_private_for_development(self):
        with TemporaryDirectory() as root, override_settings(
            PRIVATE_STORAGE_BACKEND="filesystem",
            TICKETS_STORAGE_ROOT=root,
        ):
            storage = get_ticket_storage()
            self.assertIsInstance(storage, PrivateTicketFileSystemStorage)
            self.assertIsNone(storage.base_url)
            self.assertEqual(storage.location, root)

    @override_settings(
        PRIVATE_STORAGE_BACKEND="s3",
        PRIVATE_STORAGE_S3_BUCKET="test-private-bucket",
        PRIVATE_STORAGE_S3_ENDPOINT_URL="https://objects.example.test",
        PRIVATE_STORAGE_S3_REGION_NAME="us-east-1",
        PRIVATE_STORAGE_S3_ACCESS_KEY_ID="test-access-key",
        PRIVATE_STORAGE_S3_SECRET_ACCESS_KEY="test-secret-key",
        PRIVATE_STORAGE_S3_QUERYSTRING_EXPIRE=300,
    )
    def test_s3_storage_is_private_and_has_no_public_url(self):
        storage = get_voucher_storage()
        self.assertEqual(storage.bucket_name, "test-private-bucket")
        with self.assertRaises(SuspiciousOperation):
            storage.url("vouchers/private.pdf")

    @override_settings(
        DEBUG=True,
        PRIVATE_STORAGE_BACKEND="filesystem",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_USE_TLS=False,
        EMAIL_USE_SSL=False,
        EMAIL_PORT=2525,
        EMAIL_TIMEOUT=10,
        EMAIL_CONFIGURATION_ERRORS=[],
    )
    def test_locmem_email_configuration_is_accepted_for_tests(self):
        self.assertEqual(private_storage_and_email_configuration(None), [])
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        DEBUG=True,
        EMAIL_USE_TLS=True,
        EMAIL_USE_SSL=True,
        EMAIL_PORT=2525,
        EMAIL_TIMEOUT=10,
        EMAIL_CONFIGURATION_ERRORS=[],
    )
    def test_tls_and_ssl_are_mutually_exclusive(self):
        issues = private_storage_and_email_configuration(None)
        self.assertTrue(any(isinstance(issue, Error) and issue.id == "tickets.E015" for issue in issues))

    @override_settings(PRIVATE_STORAGE_BACKEND="filesystem")
    def test_production_filesystem_storage_is_rejected(self):
        with mock.patch.object(settings, "DEBUG", False), mock.patch.object(settings, "TESTING", False):
            issues = private_storage_and_email_configuration(None)
        self.assertTrue(any(issue.id == "tickets.E008" for issue in issues))
