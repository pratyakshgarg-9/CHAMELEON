"""Verifies the mTLS mechanism itself: a real TLS server (not TestClient's
in-process ASGI transport, which never touches the network layer) requiring
and verifying client certs against a CA. Mirrors a manual check done while
planning this feature: a client with no cert, or a cert signed by an
untrusted CA, gets rejected at the TLS handshake before any route code runs;
a client with a cert signed by the trusted CA gets through normally.
"""

import socket
import ssl
import threading
import time

import httpx
import pytest
import uvicorn

from main import app
from scripts.generate_dev_certs import generate_key, self_signed_ca, sign_cert, write_cert, write_key


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def running_server(tmp_path):
    ca_key = generate_key()
    ca_cert = self_signed_ca(ca_key)
    ca_cert_path = tmp_path / "trusted_ca.crt"
    write_cert(ca_cert_path, ca_cert)

    server_key_obj, server_cert_obj = sign_cert("test-server", ca_key, ca_cert)
    server_key_path = tmp_path / "server.key"
    server_cert_path = tmp_path / "server.crt"
    write_key(server_key_path, server_key_obj)
    write_cert(server_cert_path, server_cert_obj)

    trusted_client_key_obj, trusted_client_cert_obj = sign_cert("regA-c1-edge2", ca_key, ca_cert)
    trusted_client_key_path = tmp_path / "trusted_client.key"
    trusted_client_cert_path = tmp_path / "trusted_client.crt"
    write_key(trusted_client_key_path, trusted_client_key_obj)
    write_cert(trusted_client_cert_path, trusted_client_cert_obj)

    other_ca_key = generate_key()
    other_ca_cert = self_signed_ca(other_ca_key)
    untrusted_client_key_obj, untrusted_client_cert_obj = sign_cert("regA-c1-edge3", other_ca_key, other_ca_cert)
    untrusted_client_key_path = tmp_path / "untrusted_client.key"
    untrusted_client_cert_path = tmp_path / "untrusted_client.crt"
    write_key(untrusted_client_key_path, untrusted_client_key_obj)
    write_cert(untrusted_client_cert_path, untrusted_client_cert_obj)

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        ssl_certfile=str(server_cert_path),
        ssl_keyfile=str(server_key_path),
        ssl_ca_certs=str(ca_cert_path),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        log_level="error",
        lifespan="off",  # node-agent's own background tasks aren't needed for this check
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("test mTLS server did not start in time")

    try:
        yield {
            "url": f"https://127.0.0.1:{port}/health",
            "ca_cert_path": str(ca_cert_path),
            "trusted_client_cert_path": str(trusted_client_cert_path),
            "trusted_client_key_path": str(trusted_client_key_path),
            "untrusted_client_cert_path": str(untrusted_client_cert_path),
            "untrusted_client_key_path": str(untrusted_client_key_path),
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_valid_client_cert_is_accepted(running_server):
    ctx = ssl.create_default_context(cafile=running_server["ca_cert_path"])
    ctx.load_cert_chain(
        certfile=running_server["trusted_client_cert_path"],
        keyfile=running_server["trusted_client_key_path"],
    )
    with httpx.Client(verify=ctx) as client:
        resp = client.get(running_server["url"])
    assert resp.status_code == 200


def test_missing_client_cert_is_rejected(running_server):
    ctx = ssl.create_default_context(cafile=running_server["ca_cert_path"])
    with httpx.Client(verify=ctx) as client:
        with pytest.raises(httpx.TransportError):
            client.get(running_server["url"])


def test_cert_from_untrusted_ca_is_rejected(running_server):
    ctx = ssl.create_default_context(cafile=running_server["ca_cert_path"])
    ctx.load_cert_chain(
        certfile=running_server["untrusted_client_cert_path"],
        keyfile=running_server["untrusted_client_key_path"],
    )
    with httpx.Client(verify=ctx) as client:
        with pytest.raises(httpx.TransportError):
            client.get(running_server["url"])
