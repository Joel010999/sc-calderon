from django.conf import settings


def get_max_passengers_per_booking():
    return getattr(settings, "SALES_MAX_PASSENGERS_PER_BOOKING", 4)


def get_online_hold_minutes():
    return getattr(settings, "SALES_ONLINE_HOLD_MINUTES", 15)


def get_online_cutoff_minutes():
    return getattr(settings, "SALES_ONLINE_CUTOFF_MINUTES", 60)


def get_manual_hold_hours():
    return getattr(settings, "SALES_MANUAL_HOLD_HOURS", 24)
