from django.apps import AppConfig


class HCMUSConfig(AppConfig):
    name = 'hcmus'
    verbose_name = 'FIT-HCMUS'

    def ready(self):
        # Nạp signal ghi vết khi participation bị xoá (xem hcmus/audit.py).
        from hcmus import audit  # noqa: F401
