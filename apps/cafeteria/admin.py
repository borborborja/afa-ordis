from django.contrib import admin
from django.contrib.auth.models import Group

from .models import (
    AcademicYear,
    AuditEvent,
    CourseClosure,
    CourseGroup,
    DailyReport,
    DailyReportRecipient,
    Diet,
    Family,
    FamilyMembership,
    Invitation,
    MealBooking,
    MealSettings,
    MonthlyStatement,
    PriceRule,
    ServiceDay,
    StatementLine,
    Student,
    UserProfile,
)


class FamilyMembershipInline(admin.TabularInline):
    model = FamilyMembership
    extra = 0


class StudentInline(admin.TabularInline):
    model = Student
    extra = 0
    fields = ("first_name", "last_name", "course_group", "meal_plan", "is_scholarship", "active")


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ("name", "billing_email", "phone", "active", "monthly_email_enabled")
    search_fields = ("name", "billing_email", "students__first_name", "students__last_name")
    inlines = [FamilyMembershipInline, StudentInline]


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "family", "course_group", "meal_plan", "is_scholarship", "active")
    list_filter = ("active", "is_scholarship", "meal_plan", "course_group")
    search_fields = ("first_name", "last_name", "family__name")


class DailyRecipientInline(admin.TabularInline):
    model = DailyReportRecipient
    extra = 1


@admin.register(MealSettings)
class MealSettingsAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "daily_cutoff", "daily_reports_enabled", "monthly_preparation_day")
    inlines = [DailyRecipientInline]


@admin.register(MealBooking)
class MealBookingAdmin(admin.ModelAdmin):
    list_display = ("date", "student", "diet_name", "status", "unit_price", "updated_at")
    list_filter = ("status", "date")
    search_fields = ("student__first_name", "student__last_name", "student__family__name")
    date_hierarchy = "date"


@admin.register(MonthlyStatement)
class MonthlyStatementAdmin(admin.ModelAdmin):
    list_display = ("family", "month", "year", "total", "status", "prepared_at", "sent_at")
    list_filter = ("status", "year", "month")
    search_fields = ("family__name",)
    readonly_fields = ("total", "prepared_at", "closed_at", "sent_at")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "target_type", "target_id")
    list_filter = ("action", "target_type")
    search_fields = ("actor__email", "target_id")
    readonly_fields = ("created_at", "actor", "action", "target_type", "target_id", "details")


admin.site.register([
    AcademicYear,
    CourseGroup,
    Diet,
    ServiceDay,
    CourseClosure,
    PriceRule,
    DailyReport,
    StatementLine,
    Invitation,
    UserProfile,
])
admin.site.site_header = "Administració AFA Ordis"
admin.site.site_title = "AFA Ordis"
admin.site.index_title = "Gestió del portal"
