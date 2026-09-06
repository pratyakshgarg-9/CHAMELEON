"""Generate throwaway mTLS certs for local dev/testing only.

This is NOT Member 3's real CA (see /shared/certs/README_2.md) — it's a
disposable CA generated on your own machine so the mTLS mechanism in
app/clients.py and main.py can be exercised end-to-end before real
per-node certs exist. Swapping to the real ones later is a config change
(CA_CERT_PATH / CLIENT_CERT_PATH / CLIENT_KEY_PATH in .env), not a rewrite.

Everything this script writes goes under an output directory that's
already gitignored (certs/ by default) — nothing here should ever be
committed, including the fake dev CA's private key.

Usage:
    python scripts/generate_dev_certs.py --node-id regA-c1-edge1
    python scripts/generate_dev_certs.py --node-id regA-c1-edge2 --out-dir ./certs2
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CA_CERT_NAME = "dev_ca.crt"
CA_KEY_NAME = "dev_ca.key"
VALID_DAYS = 30


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def self_signed_ca(key: rsa.RSAPrivateKey) -> x509.Certificate:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chameleon-dev-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )


def sign_cert(node_id: str, ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = generate_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--node-id", required=True, help="Becomes the cert's CN, e.g. regA-c1-edge1")
    parser.add_argument("--out-dir", default="./certs", help="Output directory (default: ./certs)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ca_cert_path = out_dir / CA_CERT_NAME
    ca_key_path = out_dir / CA_KEY_NAME

    if ca_cert_path.exists() and ca_key_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        print(f"Reusing existing dev CA at {ca_cert_path}")
    else:
        ca_key = generate_key()
        ca_cert = self_signed_ca(ca_key)
        write_key(ca_key_path, ca_key)
        write_cert(ca_cert_path, ca_cert)
        print(f"Created new dev CA at {ca_cert_path} (valid {VALID_DAYS} days)")

    node_key, node_cert = sign_cert(args.node_id, ca_key, ca_cert)
    node_key_path = out_dir / "node.key"
    node_cert_path = out_dir / "node.crt"
    write_key(node_key_path, node_key)
    write_cert(node_cert_path, node_cert)

    print(f"Issued dev cert for node_id={args.node_id!r}")
    print(f"  cert: {node_cert_path}")
    print(f"  key:  {node_key_path}")
    print()
    print("Point your .env at these (already the default CLIENT_CERT_PATH/")
    print("CLIENT_KEY_PATH if --out-dir is ./certs):")
    print(f"  CA_CERT_PATH={ca_cert_path}")
    print(f"  CLIENT_CERT_PATH={node_cert_path}")
    print(f"  CLIENT_KEY_PATH={node_key_path}")
    print("  MTLS_ENABLED=true")


if __name__ == "__main__":
    main()
