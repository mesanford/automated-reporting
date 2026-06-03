"""OAuth `state` round-trip: workspace_id encoded on login, validated on
callback. Legacy state format still decodes."""
from app.api.oauth import _decode_state, _encode_state


class TestEncode:
    def test_v2_with_workspace(self):
        assert _encode_state("google", workspace_id=42, connection_id=None) == "v2:google:42"

    def test_v2_with_workspace_and_reconnect(self):
        assert _encode_state("meta", workspace_id=7, connection_id=99) == "v2:meta:7:99"

    def test_legacy_no_workspace(self):
        assert _encode_state("google", workspace_id=None, connection_id=None) == "google"

    def test_legacy_no_workspace_reconnect(self):
        assert _encode_state("google", workspace_id=None, connection_id=12) == "google:12"


class TestDecode:
    def test_v2_full(self):
        assert _decode_state("v2:google:42:99") == ("google", 42, 99)

    def test_v2_no_reconnect(self):
        assert _decode_state("v2:linkedin:5") == ("linkedin", 5, None)

    def test_legacy_no_reconnect(self):
        assert _decode_state("tiktok") == ("tiktok", None, None)

    def test_legacy_with_reconnect(self):
        assert _decode_state("meta:33") == ("meta", None, 33)


def test_roundtrip():
    cases = [
        ("google", 1, None),
        ("meta", 7, 99),
        ("linkedin", None, None),
        ("tiktok", None, 12),
    ]
    for platform, ws, conn in cases:
        encoded = _encode_state(platform, ws, conn)
        decoded = _decode_state(encoded)
        assert decoded == (platform, ws, conn), f"{encoded} → {decoded}"
