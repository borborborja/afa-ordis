from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    BookingStatus,
    CourseClosure,
    DailyReport,
    MealBooking,
    MealType,
    UserProfile,
)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=CourseClosure)
def mark_report_outdated_for_course_closure(sender, instance, created, **kwargs):
    """Excursions inform the calendar; they never cancel a family's reservation."""
    if created:
        MealBooking.objects.filter(
            student__course_group=instance.course_group,
            date=instance.date,
            status=BookingStatus.ACTIVE,
        ).update(meal_type=MealType.PACKED_LUNCH)
    DailyReport.objects.filter(date=instance.date).update(is_outdated=True)
