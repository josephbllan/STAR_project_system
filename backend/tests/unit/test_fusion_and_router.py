from uuid import uuid4

from retrieval.encoders.deterministic import DeterministicEncoder
from retrieval.fusion import fuse
from retrieval.normalisation import is_normalised
from retrieval.query_router import Spectrum, route_image
from retrieval.types import EncoderRef, ScoredCandidate


def test_deterministic_encoder_emits_a_unit_vector() -> None:
    encoder = DeterministicEncoder("clip", 512)
    values = encoder.encode_image(b"probe")
    assert len(values) == 512
    assert is_normalised(values)


def test_identical_bytes_produce_identical_vectors() -> None:
    encoder = DeterministicEncoder("clip", 512)
    assert encoder.encode_image(b"same") == encoder.encode_image(b"same")


def test_fusion_prefers_the_higher_weighted_encoder() -> None:
    content = uuid4()
    clip = EncoderRef("openai/clip-vit-base-patch32", 512)
    dino = EncoderRef("vit_small_patch14_dinov2.lvd142m", 384)
    fused = fuse(
        [
            ScoredCandidate(content, clip, 0.2),
            ScoredCandidate(content, dino, 0.9),
        ],
        model_weight=1.0,
        metadata_weight=0.0,
    )
    assert fused[0].fused_score == 0.9


def test_low_variance_bytes_route_as_infrared() -> None:
    assert route_image(bytes([40] * 200)) is Spectrum.INFRARED


def test_varied_bytes_route_as_visible() -> None:
    assert route_image(bytes(range(256))) is Spectrum.VISIBLE
