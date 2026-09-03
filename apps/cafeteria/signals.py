from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    BookingStatus,
    CourseClosure,
    DailyReport,
    MealBooking,
    UserProfile,
    log_event,
)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=CourseClosure)
def cancel_bookings_for_course_closure(sender, instance, created, **kwargs):
    if not created:
        return
    bookings = MealBooking.objects.filter(
        student__course_group=instance.course_group,
        date=instance.date,
        status=BookingStatus.ACTIVE,
    )
    family_ids = set()
    for booking in bookings:
        family_ids.add(booking.student.family_id)
        booking.status = BookingStatus.CANCELLED
        booking.override_reason = f"{instance.title}: dia sense servei"
        booking.save(update_fields=["status", "override_reason", "updated_at"])
        log_event(None, "booking.cancelled_for_course_closure", booking, {"closure": instance.title})
    DailyReport.objects.filter(date=instance.date).update(is_outdated=True)
    transaction.on_commit(lambda: __import__("apps.cafeteria.tasks", fromlist=["send_course_closure_notification"]).send_course_closure_notification(instance.pk, list(family_ids)))
