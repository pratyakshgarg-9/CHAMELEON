"""Issue mTLS certs for CHAMELEON services from one shared CA.

Deadline-driven stand-in for /shared/certs/README_2.md's CSR-exchange
process (only Member 3 holds the CA key, everyone else sends her a CSR) —
that assumes a multi-day back-and-forth this project doesn't have time for
right now. This generates one real CA locally and issues every service's
cert from it in one shot. The CA private key never leaves this machine and
is gitignored, same as every other service's key. This is a documented,
deliberate scope call for hitting the review deadline, not a replacement
for the README_2.md process long-term.

Reuses the exact cert-issuing logic node-agent's own
scripts/generate_dev_certs.py already had (generate_key, self_signed_ca,
sign_cert, write_key, write_cert) — same functions, same behavior, just
usable for every service's identity instead of only node-agent's.

Usage:
    python scripts/issue_certs.py --node-id regA-c1-edge1 --out-dir node-agent/certs
    python scripts/issue_certs.py --node-id advisor --out-dir advisor/certs
    python scripts/issue_certs.py --node-id trust-service --out-dir trust-service/certs
    python scripts/issue_certs.py --node-id coordinator --out-dir coordinator/certs

    Or issue everything used by the local 4-service demo in one call:
    python scripts/issue_certs.py --all
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

REPO_ROOT = Path(__file__).resolve().parent.parent
CA_CERT_NAME = "ca.crt"
CA_KEY_NAME = "ca.key"
VALID_DAYS = 30

# Identity -> its own certs/ directory, for the --all convenience path.
ALL_IDENTITIES = {
    "regA-c1-edge1": REPO_ROOT / "node-agent" / "certs",
    "advisor": REPO_ROOT / "advisor" / "certs",
    "trust-service": REPO_ROOT / "trust-service" / "certs",
    "coordinator": REPO_ROOT / "coordinator" / "certs",
}
# shared/certs/ca.crt matches node-agent/.env.example's existing
# CA_CERT_PATH default (../shared/certs/ca.crt) exactly, so every service
# picks up the real CA with zero config once MTLS_ENABLED is flipped on.
# The cert itself is safe to commit per README_2.md; ca.key never is
# (gitignored).
SHARED_CA_DIR = REPO_ROOT / "shared" / "certs"


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def self_signed_ca(key: rsa.RSAPrivateKey) -> x509.Certificate:
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "chameleon-ca")])
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


def sign_cert(
    node_id: str, ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
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
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]
            ),
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


def load_or_create_ca(ca_dir: Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate, Path]:
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_cert_path = ca_dir / CA_CERT_NAME
    ca_key_path = ca_dir / CA_KEY_NAME

    if ca_cert_path.exists() and ca_key_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        print(f"Reusing existing shared CA at {ca_cert_path}")
    else:
        ca_key = generate_key()
        ca_cert = self_signed_ca(ca_key)
        write_key(ca_key_path, ca_key)
        write_cert(ca_cert_path, ca_cert)
        print(f"Created new shared CA at {ca_cert_path} (valid {VALID_DAYS} days)")

    return ca_key, ca_cert, ca_cert_path


def issue(node_id: str, out_dir: Path, ca_key, ca_cert, ca_cert_path: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    node_key, node_cert = sign_cert(node_id, ca_key, ca_cert)
    node_key_path = out_dir / "node.key"
    node_cert_path = out_dir / "node.crt"
    write_key(node_key_path, node_key)
    write_cert(node_cert_path, node_cert)

    print(f"Issued cert for {node_id!r}")
    print(f"  cert: {node_cert_path}")
    print(f"  key:  {node_key_path}")
    print(f"  ca:   {ca_cert_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--node-id", help="Identity to issue a cert for (becomes the cert's CN)")
    parser.add_argument("--out-dir", help="Output directory for this identity's cert/key")
    parser.add_argument(
        "--all", action="store_true", help="Issue certs for every identity used by the local 4-service demo"
    )
    args = parser.parse_args()

    if not args.all and not (args.node_id and args.out_dir):
        parser.error("either --all, or both --node-id and --out-dir")

    ca_key, ca_cert, ca_cert_path = load_or_create_ca(SHARED_CA_DIR)

    if args.all:
        for node_id, out_dir in ALL_IDENTITIES.items():
            issue(node_id, out_dir, ca_key, ca_cert, ca_cert_path)
            print()
    else:
        issue(args.node_id, Path(args.out_dir), ca_key, ca_cert, ca_cert_path)

    print(f"CA cert for every .env's CA_CERT_PATH: {ca_cert_path}")


if __name__ == "__main__":
    main()
