"""The OPC UA server driver's security options actually apply (HP-29).

`opcua_server.py` chose `NoSecurity` unconditionally while its own comment said
"deployments that need certificates can configure them" -- and nothing anywhere
read an option. Either the options work or the comment goes; these are the
checks that decide which.

The default is the thing most worth protecting: **anonymous and unencrypted on
loopback**, deliberately, because this is a teaching tool. Every test here
either asserts that default is untouched or configures something explicitly.

Own file rather than an addition to `tests/test_opcua.py`: two other streams are
working in this tree and the driver, not that suite, is the thing this agent
owns.
"""

from __future__ import annotations

import asyncio
import datetime
import socket
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from asyncua import Client, ua

from factoryforge_sidecar import drivers

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """A port nothing else is on, so two suites can share a machine (HP-53)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _endpoint() -> str:
    return f"opc.tcp://127.0.0.1:{_free_port()}/factoryforge/"


@pytest_asyncio.fixture
async def started(bus, mock):
    """Start a configured opcua-server driver and stop it afterwards."""
    running: list = []

    async def start(**options):
        driver = drivers.create("opcua-server", bus, endpoint=_endpoint(), **options)
        running.append(driver)
        await driver.start()
        await asyncio.sleep(0)      # let asyncua's listener task reach accept()
        return driver

    try:
        yield start
    finally:
        for driver in running:
            await driver.stop()


def _self_signed(tmp_path: Path) -> tuple[str, str]:
    """A throwaway server certificate and key.

    Generated rather than committed: a checked-in key is a key, and one with a
    fixed expiry is a test that starts failing on a date nobody chose.
    """
    crypto = pytest.importorskip("cryptography")     # asyncua depends on it
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FactoryForge test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName(
            [x509.UniformResourceIdentifier("urn:factoryforge:test")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server.der"
    key_path = tmp_path / "server.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    return str(cert_path), str(key_path)


# --- the default, which must not move ---------------------------------------


async def test_the_default_is_anonymous_and_unencrypted_on_loopback(started):
    """The documented default, asserted rather than assumed.

    Every other test here turns something on; this one is the reason they all
    have to be opt-in.
    """
    driver = await started()
    assert driver.security == [ua.SecurityPolicyType.NoSecurity]
    assert driver.server._security_policy == [ua.SecurityPolicyType.NoSecurity]

    client = Client(driver.endpoint, timeout=10)
    async with client:                      # no user, no password, no policy
        assert await client.get_namespace_array()


# --- certificates ------------------------------------------------------------


async def test_a_secure_policy_without_a_certificate_is_refused(started):
    """A policy that cannot be honoured is refused before anything listens.

    Starting anyway would advertise encryption and then fail every handshake,
    which from a SCADA package reads as a broken server rather than a
    misconfigured one.
    """
    with pytest.raises(ValueError, match="certificate"):
        await started(security="Basic256Sha256_SignAndEncrypt")


async def test_an_unknown_policy_names_the_real_ones(bus, mock):
    """A typo must not fall back to NoSecurity: that is HP-29 again, quieter."""
    with pytest.raises(ValueError, match="Basic256Sha256_SignAndEncrypt"):
        drivers.create("opcua-server", bus, endpoint=_endpoint(),
                       security="Basic256Sha255_SignAndEncrypt")


async def test_a_certificate_reaches_the_endpoint_the_server_advertises(
        started, tmp_path):
    """HP-29's own verification line: start with a certificate and confirm the
    endpoint offers it.

    `connect_and_get_server_endpoints` is the discovery path every OPC UA client
    takes before choosing a policy, so this is what a real client would see --
    not an inspection of our own object.
    """
    cert, key = _self_signed(tmp_path)
    driver = await started(security="Basic256Sha256_SignAndEncrypt",
                           certificate=cert, private_key=key)

    endpoints = await Client(driver.endpoint, timeout=10).connect_and_get_server_endpoints()
    offered = {e.SecurityPolicyUri for e in endpoints}
    assert any("Basic256Sha256" in uri for uri in offered), offered
    assert not any(uri.endswith("#None") for uri in offered), (
        f"the endpoint still offers an unsecured policy: {offered} -- a server "
        f"that offers both is as open as one that offers neither")
    assert all(e.ServerCertificate for e in endpoints), (
        "the endpoint advertises no server certificate, so no client can "
        "encrypt to it")


# --- authentication ----------------------------------------------------------


async def test_a_configured_password_is_required(started):
    """With a credential configured, anonymous is refused and so is a wrong one.

    Deliberately over the unencrypted endpoint -- that is the configuration a
    teaching deployment on a lab network reaches for first, and the driver warns
    on the bus that the password crosses the wire in clear.
    """
    driver = await started(username="lab", password="hunter2",
                           allow_anonymous=False)

    with pytest.raises(Exception):          # asyncua: BadUserAccessDenied / BadIdentityTokenRejected
        async with Client(driver.endpoint, timeout=10):
            pass

    wrong = Client(driver.endpoint, timeout=10)
    wrong.set_user("lab")
    wrong.set_password("hunter1")
    with pytest.raises(Exception):
        async with wrong:
            pass

    right = Client(driver.endpoint, timeout=10)
    right.set_user("lab")
    right.set_password("hunter2")
    async with right:
        assert await right.get_namespace_array()


async def test_anonymous_still_works_alongside_a_credential(started):
    """`allow_anonymous` defaults to true, and a credential does not flip it.

    The student who was connecting yesterday keeps connecting today; locking
    them out is a separate, explicit decision.
    """
    driver = await started(username="lab", password="hunter2")
    async with Client(driver.endpoint, timeout=10) as anonymous:
        assert await anonymous.get_namespace_array()


async def test_locking_out_anonymous_without_a_credential_is_refused(started):
    """Otherwise the server starts and nobody on earth can connect to it."""
    with pytest.raises(ValueError, match="username"):
        await started(allow_anonymous=False)


# --- the options are reachable from the command line -------------------------


def test_the_new_options_are_typed_for_the_cli():
    """`-o` hands over strings, and a type hint does not make one a bool.

    `option_types` reads the constructor's annotations, which is what the CLI
    coerces from -- so an option that is not annotated is an option that arrives
    as the string "false" and is true (gotcha 19b).
    """
    types = drivers.option_types("opcua-server")
    assert types["security"] == "str"
    assert types["certificate"] == "str"
    assert types["private_key"] == "str"
    assert types["username"] == "str"
    assert types["password"] == "str"
    assert types["allow_anonymous"] == "bool"
