import ssl

import app.clients as clients
from app.config import settings


def test_ssl_context_disables_hostname_check_when_mtls_enabled(monkeypatch):
    # Peers are addressed by Tailscale IP; our certs' identity is node_id
    # (CN), not a SAN for that IP — hostname verification must be off, or
    # every real peer connection fails with CERTIFICATE_VERIFY_FAILED
    # regardless of whether the cert is legitimate (this broke the actual
    # AWS deployment before the fix).
    monkeypatch.setattr(settings, "MTLS_ENABLED", True)
    monkeypatch.setattr(clients, "_ssl_context", None)

    ctx = clients._get_ssl_context()

    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_ssl_context_is_true_when_mtls_disabled(monkeypatch):
    monkeypatch.setattr(settings, "MTLS_ENABLED", False)
    monkeypatch.setattr(clients, "_ssl_context", None)

    assert clients._get_ssl_context() is True
