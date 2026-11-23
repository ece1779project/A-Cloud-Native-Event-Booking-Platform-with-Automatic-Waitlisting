from ipaddress import ip_network

from app.api.middleware import normalize_ip, resolve_client_ip

PROXIES = (ip_network("10.0.0.0/8"),)


def test_untrusted_peer_ignores_forwarded_header():
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", PROXIES) == "203.0.113.9"
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", ()) == "203.0.113.9"


def test_trusted_proxy_uses_rightmost_untrusted_hop():
    assert (
        resolve_client_ip("10.0.0.2", "6.6.6.6, 198.51.100.7, 10.0.0.5", PROXIES)
        == "198.51.100.7"
    )


def test_trusted_proxy_without_header_or_garbage():
    assert resolve_client_ip("10.0.0.2", None, PROXIES) == "10.0.0.2"
    assert resolve_client_ip("10.0.0.2", "not-an-ip", PROXIES) == "10.0.0.2"


def test_normalize_ip():
    assert normalize_ip("::ffff:192.0.2.1") == "192.0.2.1"
    assert normalize_ip("2001:DB8:0:0::1") == "2001:db8::1"
    assert normalize_ip("bogus") is None
