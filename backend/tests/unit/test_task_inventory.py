from apps.tasks.inventory import INVENTORY, INVENTORY_BY_NAME, VISIBILITY_TIMEOUT_SECONDS


def test_inventory_names_are_unique() -> None:
    names = [spec.name for spec in INVENTORY]
    assert len(names) == len(set(names))
    assert set(INVENTORY_BY_NAME) == set(names)


def test_visibility_timeout_exceeds_every_hard_limit() -> None:
    """A message must not be redelivered while its worker is still inside the hard limit."""
    longest = max(spec.time_limit for spec in INVENTORY)
    assert longest < VISIBILITY_TIMEOUT_SECONDS


def test_the_celery_registry_matches_the_inventory() -> None:
    from config.celery import app

    app.loader.import_default_modules()
    registered = {name for name in app.tasks if not name.startswith("celery.")}
    assert registered == set(INVENTORY_BY_NAME)
