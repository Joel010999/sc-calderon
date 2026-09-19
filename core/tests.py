from django.apps import apps
from django.test import SimpleTestCase

from .test_checkout import (
    ArchitectureBoundaryTests,
    BookingSummaryViewTests,
    CheckoutViewTests,
    CreatePublicBookingViewTests,
    HomeViewTests,
    SalesOnlineBookingServiceTests,
    SearchTripsViewTests,
)


class DomainRegistryTests(SimpleTestCase):
    def test_legacy_core_models_are_not_registered(self):
        for name in ("Ruta", "Parada", "Bus", "Salida", "Reserva", "ReservaItem"):
            with self.subTest(model=name), self.assertRaises(LookupError):
                apps.get_model("core", name)

    def test_operations_models_remain_registered(self):
        for name in ("Stop", "Route", "RouteStop", "Bus", "Seat", "Trip", "TripStop", "TripFare"):
            with self.subTest(model=name):
                model = apps.get_model("operations", name)
                self.assertEqual(model._meta.app_label, "operations")
                self.assertEqual(model.__name__, name)
