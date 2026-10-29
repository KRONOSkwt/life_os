"""Local TLS identity; clients verify its certificate fingerprint out of band."""

import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def normalized_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        name = host.encode("idna").decode("ascii").lower()
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if len(name) > 253 or not re.fullmatch(rf"{label}(?:\.{label})*", name):
            raise ValueError("Use a hostname or IP address without scheme, port or wildcard")
        return name


def create_identity(directory: Path, hosts: list[str], *, now=None) -> dict:
    names = sorted({normalized_host(host) for host in ["localhost", "127.0.0.1", "::1", *hosts]})
    alternatives = []
    for name in names:
        try:
            alternatives.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alternatives.append(x509.DNSName(name))
    key = ec.generate_private_key(ec.SECP256R1())
    current = now or datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Life OS personal server")])
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(minutes=5)).not_valid_after(current + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                    key_encipherment=False, data_encipherment=False,
                                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                                    encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    # Refuse to overwrite any existing identity, including a partially written one.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    key_path = directory / "server-key.pem"
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    with (directory / "server-cert.pem").open("xb") as output:
        output.write(certificate.public_bytes(serialization.Encoding.PEM))
    return identity_info(certificate)


def load_identity(directory: Path) -> x509.Certificate:
    certificate = x509.load_pem_x509_certificate((directory / "server-cert.pem").read_bytes())
    key = serialization.load_pem_private_key((directory / "server-key.pem").read_bytes(), password=None)
    encoding = serialization.Encoding.DER
    public_format = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(encoding, public_format) != certificate.public_key().public_bytes(encoding, public_format):
        raise ValueError("TLS certificate and private key do not match")
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
        raise ValueError("TLS certificate is expired or not yet valid")
    return certificate


def covers_host(certificate: x509.Certificate, host: str) -> bool:
    expected = normalized_host(host)
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return any(str(name.value).lower() == expected for name in names)


def identity_info(certificate: x509.Certificate) -> dict:
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {
        "certificate_sha256": certificate.fingerprint(hashes.SHA256()).hex(),
        "certificate_expires_at": certificate.not_valid_after_utc.isoformat(),
        "hosts": [str(name.value) for name in names],
    }
