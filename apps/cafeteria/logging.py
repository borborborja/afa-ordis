"""Production diagnostics retain component, severity and exception type, not payloads."""
import logging

from django.views.debug import SafeExceptionReporterFilter


class MetadataOnlyFilter(logging.Filter):
    def filter(self, record):
        kind = record.exc_info[0].__name__ if record.exc_info else "event"
        status = getattr(record, "status_code", None)
        record.msg = "component=%s type=%s status=%s"
        record.args = (record.name, kind, status if isinstance(status, int) else "-")
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class PrivacyExceptionReporterFilter(SafeExceptionReporterFilter):
    def get_traceback_frame_variables(self, request, tb_frame):
        return [(name, self.cleansed_substitute) for name in tb_frame.f_locals]

    def get_post_parameters(self, request):
        return {name: self.cleansed_substitute for name in request.POST}
