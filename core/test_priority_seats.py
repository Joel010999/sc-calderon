from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse

from operations.models import Seat, SeatCategory
from sales.models import Booking
from sales.priority_seats import priority_seat_release_at

from .test_checkout import CheckoutBaseTestCase


class PublicPrioritySeatPostTests(CheckoutBaseTestCase):
    def test_manipulated_post_cannot_create_priority_seat_before_release(self):
        priority_seat = Seat.objects.create(
            bus=self.bus,
            number=51,
            deck=Seat.Deck.UPPER,
            category=SeatCategory.CAMA,
            position_x=2,
            position_y=0,
        )
        release_at = priority_seat_release_at(self.trip_out)
        before_release = release_at - timedelta(seconds=1)
        payload = {
            "trip_type": "oneway",
            "passengers_count": "1",
            "outbound_trip": str(self.trip_out.pk),
            "outbound_origin": str(self.ts_out_orig.pk),
            "outbound_destination": str(self.ts_out_dest.pk),
            "outbound_seats": [str(priority_seat.pk)],
            "email": "manipulado@correo.com",
            "phone": "3881234567",
            "passenger_1_first_name": "Prueba",
            "passenger_1_last_name": "Manipulada",
            "passenger_1_document_type": "DNI",
            "passenger_1_document_number": "35123456",
            "passenger_1_birth_date": "1990-01-01",
            "passenger_1_nationality": "Argentina",
        }

        with patch("django.utils.timezone.now", return_value=before_release):
            response = self.client.post(reverse("crear_reserva"), payload)

        self.assertRedirects(response, reverse("buscar_viajes"))
        self.assertFalse(Booking.objects.exists())
