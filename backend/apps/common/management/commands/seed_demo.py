"""Synthetic corpora, encoders, accounts and one case for local screens."""

from __future__ import annotations

import io

from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image

from apps.accounts.models import Role, User
from apps.cases.services import create_case, create_run, grant_membership
from apps.datasets.models import Corpus, DataClassification
from apps.datasets.services import AlreadyRegisteredError, register_bytes
from apps.indexing.services import encode_content
from apps.search.models import CLIP_DIMENSIONS, DINOV2_DIMENSIONS, Encoder, EncoderFamily


ACCOUNTS = (
    ("analyst", Role.ANALYST),
    ("investigator", Role.INVESTIGATOR),
    ("reviewer", Role.REVIEWER),
    ("auditor", Role.AUDITOR),
    ("administrator", Role.ADMINISTRATOR),
)

CORPORA = (
    ("Ecom", "Ecom", "Commercial footwear catalogue imagery"),
    ("NDFsim", "NDFsim", "Forensic simulation imagery"),
    ("PreviousCases", "PreviousCases", "Crops from earlier investigations"),
)


def _png(colour: tuple[int, int, int], name: str) -> bytes:
    image = Image.new("RGB", (64, 64), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.name = name
    return buffer.getvalue()


class Command(BaseCommand):
    help = "Create synthetic accounts, corpora, encoders and one demo case."

    @transaction.atomic
    def handle(self, *args, **options):
        users: dict[str, User] = {}
        for username, role in ACCOUNTS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "mfa_enforced": role in Role and role != Role.ANALYST and role != Role.AUDITOR},
            )
            if created:
                user.role = role
                user.mfa_enforced = role in {Role.ADMINISTRATOR, Role.INVESTIGATOR, Role.REVIEWER}
                user.set_password(username)
                user.save()
            users[username] = user

        clip, _ = Encoder.objects.get_or_create(
            name="clip-vit-base-patch32",
            version="deterministic",
            preprocess_version="v1",
            defaults={
                "family": EncoderFamily.CLIP,
                "dimensions": CLIP_DIMENSIONS,
                "is_active": True,
            },
        )
        dinov2, _ = Encoder.objects.get_or_create(
            name="dinov2-vit-small",
            version="deterministic",
            preprocess_version="v1",
            defaults={
                "family": EncoderFamily.DINOV2,
                "dimensions": DINOV2_DIMENSIONS,
                "is_active": True,
            },
        )

        corpora = {}
        for code, name, description in CORPORA:
            corpus, _ = Corpus.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "description": description,
                    "data_classification": DataClassification.SYNTHETIC,
                },
            )
            corpora[code] = corpus

        samples = (
            ("Ecom", "red-trainer.png", (180, 40, 40)),
            ("Ecom", "blue-trainer.png", (40, 60, 180)),
            ("NDFsim", "cast-print.png", (80, 80, 80)),
            ("PreviousCases", "crop-prior.png", (140, 100, 40)),
        )
        investigator = users["investigator"]
        for code, filename, colour in samples:
            try:
                evidence = register_bytes(
                    _png(colour, filename),
                    corpus=corpora[code],
                    original_filename=filename,
                    registered_by=investigator,
                )
            except AlreadyRegisteredError as exc:
                evidence = exc.evidence
            encode_content(evidence.content, clip)
            encode_content(evidence.content, dinov2)

        case, created = (
            (create_case(owner=investigator, name="DEMO-001", reference="SYN-001", description="Synthetic demonstration case"), True)
            if not investigator.owned_cases.filter(name="DEMO-001").exists()
            else (investigator.owned_cases.get(name="DEMO-001"), False)
        )
        if created:
            grant_membership(
                case=case,
                user=users["analyst"],
                access_level="read",
                granted_by=investigator,
            )
            grant_membership(
                case=case,
                user=users["reviewer"],
                access_level="review",
                granted_by=investigator,
            )
            create_run(
                case=case,
                created_by=investigator,
                label="demo-run",
                corpus_ids=[corpus.pk for corpus in corpora.values()],
            )

        self.stdout.write(self.style.SUCCESS("Demo data is in place. Analyst login: analyst / analyst."))
