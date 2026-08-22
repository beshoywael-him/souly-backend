#!/usr/bin/env python3
"""
Make Souly reachable over HTTPS on the local network.

Why this exists: iOS only hands the microphone to a page on HTTPS or
localhost. Over plain http://192.168.x.x Safari refuses getUserMedia outright,
so the iPad can do everything except the one thing the robot is for — talking
to Souly. This mints a certificate the iPad will accept.

It creates two things in ./certs:

  souly-ca.crt / .key   a small certificate authority, valid 10 years.
                        This is what you install on the iPad, ONCE.
  souly.crt  / .key     the server certificate, signed by that CA, listing
                        every address this machine currently answers on.

Splitting it that way matters: your laptop's IP will change (DHCP, a different
router, the MiFi at the competition). When it does, re-run this script and the
server certificate is reissued — but the CA is unchanged, so the iPad stays
trusted and nobody has to touch it again.

    python scripts/make_cert.py            # detect addresses automatically
    python scripts/make_cert.py --ip 192.168.1.7 --ip 10.0.0.5
    python scripts/make_cert.py --force    # start over, new CA as well

Requires the `cryptography` package (in requirements.txt). It does NOT need
OpenSSL installed, which on Windows is the whole point.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
import subprocess
import sys
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:  # pragma: no cover - environment problem, not logic
    print("This script needs the `cryptography` package:")
    print("    pip install cryptography")
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "certs"

CA_CERT = CERT_DIR / "souly-ca.crt"
CA_KEY = CERT_DIR / "souly-ca.key"
SRV_CERT = CERT_DIR / "souly.crt"
SRV_KEY = CERT_DIR / "souly.key"

# Apple caps TLS server certificates at 825 days and will reject anything
# longer outright, however well-trusted the CA is.
SERVER_DAYS = 397
CA_DAYS = 3650


# -----------------------------------------------------------------------------
# Finding this machine's addresses
# -----------------------------------------------------------------------------

def local_ips() -> list[str]:
    """
    Every IPv4 address this machine answers on, best effort.

    The UDP trick is the reliable part: opening a socket toward a public
    address makes the OS pick the interface it would actually route through,
    which is the Wi-Fi adapter — not the VirtualBox or WSL adapter that
    hostname lookups love to return first.
    """
    found: list[str] = []

    def add(ip: str) -> None:
        if not ip or ip in found:
            return
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return
        if parsed.is_loopback or parsed.is_link_local:
            return
        found.append(ip)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       family=socket.AF_INET):
            add(info[4][0])
    except socket.gaierror:
        pass

    return found


# -----------------------------------------------------------------------------
# Certificates
# -----------------------------------------------------------------------------

def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def make_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _key()
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Souly Local CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Souly"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                       critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert, key


def make_server_cert(
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    ips: list[str],
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _key()

    # Every name and address the iPad might type. Safari validates against the
    # SAN list only — the Common Name has been ignored since iOS 13, so an
    # address missing from here fails no matter what else is right.
    alt: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    for ip in ips:
        alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
    try:
        alt.append(x509.DNSName(socket.gethostname()))
        alt.append(x509.DNSName(f"{socket.gethostname()}.local"))
    except Exception:
        pass

    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Souly"),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True,
                content_commitment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return cert, key


def load_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    cert = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
    key = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
    return cert, key


# -----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", action="append", default=[],
                    help="Address to include. Repeatable. Overrides detection.")
    ap.add_argument("--force", action="store_true",
                    help="Regenerate the CA too. The iPad must then re-trust.")
    args = ap.parse_args()

    CERT_DIR.mkdir(exist_ok=True)

    ips = args.ip or local_ips()
    if not ips:
        print("Couldn't work out this machine's network address.")
        print("Run `ipconfig`, find the Wi-Fi adapter's IPv4 Address, then:")
        print("    python scripts/make_cert.py --ip 192.168.1.7")
        return 1

    reused = False
    if CA_CERT.exists() and CA_KEY.exists() and not args.force:
        ca_cert, ca_key = load_ca()
        reused = True
    else:
        ca_cert, ca_key = make_ca()
        _write_cert(CA_CERT, ca_cert)
        _write_key(CA_KEY, ca_key)

    srv_cert, srv_key = make_server_cert(ca_cert, ca_key, ips)
    _write_cert(SRV_CERT, srv_cert)
    _write_key(SRV_KEY, srv_key)

    print()
    print("  Certificates written to " + str(CERT_DIR))
    print()
    print("  Valid for:")
    for ip in ips:
        print(f"    https://{ip}:8443/student")
    print("    https://localhost:8443/student")
    print()

    if reused:
        print("  Reused the existing CA, so the iPad is still trusted.")
        print("  Just restart the server — nothing to do on the tablet.")
    else:
        print("  NEW certificate authority. On the iPad, once:")
        print("    1. run.bat        (plain HTTP)")
        print(f"    2. Safari -> http://{ips[0]}:8000/ca.crt")
        print("    3. Allow the download, then Settings -> Profile Downloaded")
        print("       -> Install")
        print("    4. Settings -> General -> About -> Certificate Trust")
        print("       Settings -> turn ON 'Souly Local CA'")
        print("    5. Stop run.bat, start run-https.bat instead")
        print()
        print("  Step 4 is the one everyone forgets. Installing the profile")
        print("  is not the same as trusting it, and without the toggle")
        print("  Safari still refuses the microphone.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
