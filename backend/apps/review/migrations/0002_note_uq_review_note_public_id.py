"""The unique constraint `review.0001` should have carried.

A note is addressed by its public identifier and cited by one in a report, so a duplicate would make
two notes resolve to one URL. The omission was found by the schema-wide test that asserts
every table carrying a public identifier enforces its uniqueness, rather than by reading the model.

This is additive and takes no exclusive lock beyond the index build, so it is safe to apply to a
populated table under """

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0001_initial'),
        ('review', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='note',
            constraint=models.UniqueConstraint(fields=('public_id',), name='uq_review_note_public_id'),
        ),
    ]
