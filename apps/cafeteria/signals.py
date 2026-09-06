from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (
    BookingStatus,
    AcademicHoliday,
    CourseClosure,
    DailyReport,
    MealBooking,
    Student,
    TeacherMealBooking,
    UserProfile,
    log_event,
)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=CourseClosure)
def mark_report_outdated_for_course_closure(sender, instance, created, **kwargs):
    """Excursions are informative calendar events and never change reservations."""
    DailyReport.objects.filter(date=instance.date).update(is_outdated=True)


@receiver(post_save, sender=Student)
def mark_report_outdated_for_student_profile(sender, instance, **kwargs):
    """Names, diets and allergy alerts all change the operational daily list."""
    booking_dates = MealBooking.objects.filter(
        student=instance,
        status=BookingStatus.ACTIVE,
    ).values_list("date", flat=True)
    DailyReport.objects.filter(date__in=booking_dates).update(is_outdated=True)


@receiver(post_save, sender=AcademicHoliday)
def cancel_bookings_for_academic_holiday(sender, instance, created, **kwargs):
    """A school holiday means there is no meal service."""
    student_bookings = MealBooking.objects.filter(
        date__range=(instance.starts_on, instance.ends_on), status=BookingStatus.ACTIVE,
    )
    teacher_bookings = TeacherMealBooking.objects.filter(
        date__range=(instance.starts_on, instance.ends_on), status=BookingStatus.ACTIVE,
    )
    reason = f"{instance.get_holiday_type_display()}: {instance.title}"
    student_count = student_bookings.update(status=BookingStatus.CANCELLED, override_reason=reason)
    teacher_count = teacher_bookings.update(status=BookingStatus.CANCELLED, override_reason=reason)
    DailyReport.objects.filter(date__range=(instance.starts_on, instance.ends_on)).update(is_outdated=True)
    if student_count or teacher_count:
        log_event(None, "booking.cancelled_for_academic_holiday", instance, {
            "student_bookings": student_count,
            "teacher_bookings": teacher_count,
        })
