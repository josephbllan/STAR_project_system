"""Lookups registered onto Django's field classes at start-up.

Registration is global and happens once, in `CommonConfig.ready`. That is Django's documented
mechanism for this and it runs during `django.setup`, so the lookups are available before any
management command compiles a constraint to SQL.
"""

from django.db.models import CharField, TextField
from django.db.models.functions import Length


def register() -> None:
    """Makes `__length` available on text fields.

 Two check constraints need it - `ck_cases_query_text_length` and `ck_review_note_body_length` -
 and both bound a text length at the database rather than only at the serializer. The
 alternative was writing `Length(...)` expressions inline, which works but reads as machinery;
 the alternative to *both* was trusting the serializer, which leaves a management command or a
 data migration free to write a two-megabyte note.

 `Length` measures characters and not bytes, which is what `char_length` in the schema document
 means and what a length limit expressed for a human should mean.
 """
    CharField.register_lookup(Length)
    TextField.register_lookup(Length)
