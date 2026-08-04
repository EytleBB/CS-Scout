import pytest

from perfectworld_experiment import pwa_protocol


def test_canonical_query_matches_desktop_client_sorting():
    assert pwa_protocol.canonical_query({"uid": "765", "access_token": "x", "size": 3}) == (
        "access_token=x&size=3&uid=765"
    )


def test_signed_params_use_reconstructed_swap_data_input(monkeypatch):
    seen = {}

    def sign(randnum, timestamp, data):
        seen.update(randnum=randnum, timestamp=timestamp, data=data)
        return "signature"

    monkeypatch.setattr(pwa_protocol, "generate_signature", sign)
    params = pwa_protocol.build_signed_params(
        {"uid": "765", "size": 3}, randnum=123456, timestamp=1785500000
    )
    assert seen == {
        "randnum": "123456",
        "timestamp": "1785500000",
        "data": "size=3&uid=765",
    }
    assert list(params) == ["a", "r", "s", "t", "uid", "size"]


def test_post_signature_uses_exact_json_body(monkeypatch):
    seen = {}

    def sign(randnum, timestamp, data):
        seen["data"] = data
        return "signature"

    monkeypatch.setattr(pwa_protocol, "generate_signature", sign)
    params = pwa_protocol.build_signature_params(
        '{"match_id":"123"}', randnum=123456, timestamp=1785500000
    )
    assert seen["data"] == '{"match_id":"123"}'
    assert params == {
        "a": "20000",
        "r": "123456",
        "s": "signature",
        "t": "1785500000",
    }


def test_oss_header_signature_matches_current_client_vector():
    pytest.importorskip("cryptography")
    assert pwa_protocol.build_x_pwa_signature(
        "76561198123456789", "203.0.113.7", 1785500000
    ) == "1785500000-3635de9fe009908080beb4941b160a85"


def test_encrypted_response_matches_current_client_vector():
    pytest.importorskip("cryptography")
    assert pwa_protocol.decrypt_response(
        "1JTpAWq/2lClD9ACy1o1aw==", "123456"
    ) == '{"ok":true}'
