"""Closes the cycle between `datasets` and `cases`.

`datasets_evidencefile.case_id` references `cases_case`, and `cases_result.evidence_file_id`
references `datasets_evidencefile`. Neither app can therefore be created whole before the other,
so `datasets.0001` omits the column and this migration adds it once `cases.0001` exists.

The column is nullable, so this is an additive change requiring no table rewrite and no default -
which is what lets it be applied to a populated database under the expand-and-contract sequence at
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0001_initial'),
        ('datasets', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='evidencefile',
            name='case',
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='evidence_files', to='cases.case'),
        ),
        migrations.AddIndex(
            model_name='evidencefile',
            index=models.Index(condition=models.Q(('case__isnull', False)), fields=['case'], name='ix_datasets_evidencefile_case'),
        ),
    ]
