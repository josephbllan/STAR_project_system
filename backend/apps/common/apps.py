from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "apps.common"
    label = "common"

    def ready(self) -> None:
        # Imported here rather than at module level: `ready` is the point at which the app
        # registry is populated, and registering a lookup earlier is not guaranteed to be safe.
        from apps.common import lookups

        lookups.register()
