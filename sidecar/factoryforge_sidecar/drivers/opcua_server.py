"""OPC UA **server** driver: the simulator exposes its tags, others connect.

This is the higher-leverage half of OPC UA support. Node-RED, Ignition, and most
SCADA packages speak OPC UA client-side, so exposing the scene as a server
reaches MQTT, HTTP, dashboards, and cloud services through one Node-RED flow --
without this project writing a single integration. It also inverts who has to be
configured, which is what reduces support burden long term.

Node ids are `ns=2;s=<tag_id>` -- stable, readable, and derived from the tag id
so nobody has to maintain a mapping.

    sim `output` tag  (PLC writes it) -> writable node; client writes reach the bus
    sim `input`  tag  (PLC reads it)  -> read-only node, updated from push()

Client writes are detected with a server-side subscription to our own address
space, which is asyncua's supported way of doing this.

Security
--------

The default is **anonymous and unencrypted on loopback**, and that is
deliberate: this is a teaching tool, and a student whose SCADA package cannot
connect learns nothing about PLCs. Nothing below changes that default.

What the options do, once given (HP-29 -- the comment here used to say
"deployments that need certificates can configure them" while `start()` chose
`NoSecurity` unconditionally and nothing read a single option):

    -o security Basic256Sha256_SignAndEncrypt   the policies the endpoint offers
    -o certificate server.der                   the server's own certificate
    -o private_key server.pem                   and its key
    -o username lab -o password ...             username/password authentication
    -o allow_anonymous false                    and no anonymous sessions

Any policy other than `none` needs a certificate and a key -- OPC UA signs and
encrypts with the server's own certificate, so a secure policy without one is a
promise nothing can keep, and `start()` refuses rather than quietly serving an
endpoint that offers nothing. `security` takes a comma-separated list, so an
endpoint can offer more than one; the names are `asyncua`'s own
`ua.SecurityPolicyType` members, with `none` as an alias for `NoSecurity`.

What this does **not** do, stated plainly rather than implied: client
certificates are not checked against a trust list. A policy that signs and
encrypts protects the traffic and proves the *server's* identity; with no
validator set, `asyncua` accepts whatever certificate a client presents. Pair
this with `allow_anonymous false` and a password if it matters who connects, and
do not read an encrypted endpoint as an authenticated one.
"""

from __future__ import annotations

import asyncio
import hmac
import logging

from asyncua import Server, ua
from asyncua.crypto.permission_rules import User, UserRole
from asyncua.server.user_managers import UserManager

from ..tags import TagTable, TagValue
from . import Driver, register

log = logging.getLogger(__name__)

NAMESPACE_URI = "urn:factoryforge:scene"
DEFAULT_ENDPOINT = "opc.tcp://127.0.0.1:4841/factoryforge/"

#: `none` is what a person types; `NoSecurity` is what asyncua calls it.
_POLICY_ALIASES = {"none": "NoSecurity", "nosecurity": "NoSecurity"}


def _policies(spec: str) -> list[ua.SecurityPolicyType]:
    """Parse `-o security` into asyncua policy members.

    Names are matched case-insensitively against `ua.SecurityPolicyType`, and an
    unknown one is refused **with the list of real ones**: a typo that silently
    fell back to NoSecurity would be the same bug HP-29 is about, wearing a
    different hat.
    """
    known = {name.lower(): name for name in dir(ua.SecurityPolicyType)
             if not name.startswith("_") and name[0].isupper()}
    chosen: list[ua.SecurityPolicyType] = []
    for raw in spec.split(","):
        wanted = raw.strip()
        if not wanted:
            continue
        name = known.get(_POLICY_ALIASES.get(wanted.lower(), wanted).lower())
        if name is None:
            raise ValueError(
                f"unknown OPC UA security policy {wanted!r}; "
                f"choose from none, {', '.join(sorted(known.values()))}")
        chosen.append(getattr(ua.SecurityPolicyType, name))
    if not chosen:
        raise ValueError("-o security was empty; use 'none' to say so out loud")
    return chosen


class _PasswordUserManager(UserManager):
    """One username and password, and whether anonymous is still welcome.

    asyncua's default manager admits everybody, which is right for the default
    endpoint and wrong the moment somebody configures a credential.
    """

    def __init__(self, username: str, password: str, allow_anonymous: bool) -> None:
        self._username = username
        self._password = password
        self._allow_anonymous = allow_anonymous

    def get_user(self, iserver, username=None, password=None, certificate=None):
        if not username:
            # UserRole.User, not UserRole.Anonymous: this option decides who may
            # *connect*, not what they may do once in. asyncua's Anonymous role
            # is a restricted one that cannot even read, so handing it out here
            # would quietly change what the default endpoint does for every
            # client that was working yesterday -- asyncua's own permissive
            # manager gives UserRole.User to anonymous sessions, and this
            # matches it. Per-node permissions are a ruleset, and a different
            # question from authentication.
            return User(role=UserRole.User) if self._allow_anonymous else None
        # compare_digest, not ==: a password check that returns early tells the
        # caller how much of it was right.
        if (hmac.compare_digest(username, self._username)
                and hmac.compare_digest(password or "", self._password)):
            return User(role=UserRole.User)
        log.warning("refused an OPC UA session for %r: wrong credentials", username)
        return None

_VARIANT = {
    "bit": ua.VariantType.Boolean,
    "int": ua.VariantType.Int32,
    "float": ua.VariantType.Float,
}


class _WriteHandler:
    """Receives client writes to the tags the controller owns."""

    def __init__(self, driver: "OpcUaServerDriver") -> None:
        self._driver = driver

    def datachange_notification(self, node, val, data) -> None:
        tag_id = self._driver._by_node.get(node.nodeid.to_string())
        if tag_id is not None:
            self._driver._queue_write(tag_id, val)


@register("opcua-server")
class OpcUaServerDriver(Driver):
    def __init__(self, bus, endpoint: str = DEFAULT_ENDPOINT,
                 name: str = "FactoryForge", publish_interval: int = 50,
                 security: str = "none", certificate: str = "",
                 private_key: str = "", private_key_password: str = "",
                 username: str = "", password: str = "",
                 allow_anonymous: bool = True,
                 **config) -> None:
        super().__init__(bus, endpoint=endpoint, **config)
        self.endpoint = endpoint
        self.name = name
        self.publish_interval = publish_interval
        # Parsed here rather than in start(): `-o security Basic256Sha255` is a
        # typo, and the place to say so is the command line that contained it,
        # not four seconds later from inside an async task.
        self.security = _policies(security)
        self.certificate = certificate
        self.private_key = private_key
        self.private_key_password = private_key_password
        self.username = username
        self.password = password
        self.allow_anonymous = allow_anonymous

        self.server: Server | None = None
        self.idx: int | None = None
        self._folder = None
        self._nodes: dict[str, object] = {}     # tag_id -> node
        self._by_node: dict[str, str] = {}      # nodeid string -> tag_id
        self._subscription = None
        self._table: TagTable | None = None
        self._started = False
        #: Set while applying an engine update, so echoing our own write back
        #: onto the bus is suppressed.
        self._applying: set[str] = set()

    @property
    def actual_endpoint(self) -> str:
        return self.endpoint

    # --- lifecycle ---

    async def start(self) -> None:
        self._check_security()
        # A user manager only when one is asked for: passing None leaves
        # asyncua's permissive default, which is what the anonymous loopback
        # endpoint wants and what every existing deployment already has.
        user_manager = (
            _PasswordUserManager(self.username, self.password, self.allow_anonymous)
            if self.username or not self.allow_anonymous else None)
        self.server = Server(user_manager=user_manager)
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name(self.name)
        # Anonymous, unencrypted by default: this is a teaching tool bound to
        # loopback, and a student whose SCADA package cannot connect learns
        # nothing about PLCs. Every line below is a no-op unless somebody
        # passed an option asking for it (HP-29).
        self.server.set_security_policy(self.security)
        if self.certificate:
            await self.server.load_certificate(self.certificate)
        if self.private_key:
            await self.server.load_private_key(
                self.private_key, self.private_key_password or None)
        if user_manager is not None:
            tokens = [ua.UserNameIdentityToken] if self.username else []
            if self.allow_anonymous:
                tokens.append(ua.AnonymousIdentityToken)
            self.server.set_identity_tokens(tokens)
        self.idx = await self.server.register_namespace(NAMESPACE_URI)

        await self.server.start()
        self._started = True
        await self._report("info", "server_started",
                           f"OPC UA server listening on {self.endpoint} "
                           f"({self._security_summary()})")
        if self.password and ua.SecurityPolicyType.NoSecurity in self.security:
            # Worth saying every time rather than once in a manual: the
            # credential is checked, and on this endpoint it also crosses the
            # wire where anybody on the machine can read it.
            await self._report(
                "warn", "password_in_clear",
                "a password is configured but the endpoint offers NoSecurity, "
                "so it crosses the wire unencrypted -- drop 'none' from "
                "-o security, or treat this as loopback-only")
        if self._table is not None:
            await self._publish(self._table)

    def _check_security(self) -> None:
        """Refuse a secure policy that cannot be honoured.

        OPC UA signs and encrypts with the server's own certificate, so a policy
        other than NoSecurity without one is a promise nothing can keep. Saying
        so here beats starting an endpoint that advertises encryption and then
        fails every handshake -- which reads, from a SCADA package, as the
        server being broken rather than misconfigured.
        """
        if not self.allow_anonymous and not self.username:
            raise ValueError(
                "-o allow_anonymous false with no -o username leaves nothing to "
                "authenticate with, so nobody could connect at all; add a "
                "username and password")
        secure = [p for p in self.security if p != ua.SecurityPolicyType.NoSecurity]
        if not secure:
            return
        missing = [name for name, value in (("certificate", self.certificate),
                                            ("private_key", self.private_key))
                   if not value]
        if missing:
            raise ValueError(
                f"OPC UA security policy {secure[0].name} needs a server "
                f"certificate: pass {' and '.join('-o ' + m + ' <path>' for m in missing)}"
                f", or -o security none for the unencrypted default")

    def _security_summary(self) -> str:
        policies = ", ".join(p.name for p in self.security)
        if not self.username:
            return f"{policies}; anonymous"
        return (f"{policies}; user {self.username!r}"
                f"{' or anonymous' if self.allow_anonymous else ' only'}")

    async def stop(self) -> None:
        if self.server is not None and self._started:
            try:
                await self.server.stop()
            except Exception:
                log.debug("error stopping OPC UA server", exc_info=True)
        self._started = False
        self.server = None
        self._nodes.clear()
        self._by_node.clear()

    # --- address space ---

    async def rebuild(self, scene: str, epoch: int, table: TagTable) -> None:
        self._table = table
        if self._started:
            await self._publish(table)

    async def _publish(self, table: TagTable) -> None:
        """(Re)build the address space for this tag set."""
        assert self.server is not None and self.idx is not None

        if self._folder is not None:
            try:
                await self.server.delete_nodes([self._folder], recursive=True)
            except Exception:
                log.debug("could not delete previous folder", exc_info=True)
        self._nodes.clear()
        self._by_node.clear()

        self._folder = await self.server.nodes.objects.add_folder(
            ua.NodeId("tags", self.idx), "Tags")

        writable = []
        for tag in table:
            node_id = ua.NodeId(tag.id, self.idx)
            node = await self._folder.add_variable(
                node_id, tag.name, table.visible(tag.id), _VARIANT[tag.type])
            self._nodes[tag.id] = node
            self._by_node[node.nodeid.to_string()] = tag.id
            if tag.kind == "output":
                # The controller owns it, so clients may write it.
                await node.set_writable()
                writable.append(node)

        # Subscribe to our own address space to catch client writes.
        if writable:
            self._subscription = await self.server.create_subscription(
                self.publish_interval, _WriteHandler(self))
            await self._subscription.subscribe_data_change(writable)

        log.info("published %d tags at %s", len(self._nodes), self.endpoint)

    # --- data flow ---

    async def push(self, values: dict[str, TagValue]) -> None:
        """Sensor changes from the engine -> update the read-only nodes."""
        if not self._started or self._table is None:
            return
        for tag_id, value in values.items():
            node = self._nodes.get(tag_id)
            tag = self._table.get(tag_id)
            if node is None or tag is None:
                continue
            self._applying.add(tag_id)
            try:
                await node.write_value(
                    ua.DataValue(ua.Variant(value, _VARIANT[tag.type])))
            except Exception as exc:
                log.warning("could not update %s: %s", tag_id, exc)
            finally:
                self._applying.discard(tag_id)

    async def bus_disconnected(self) -> None:
        """Mark every published node Bad rather than leave it holding the last
        value it ever read from a bus that is no longer there. Unlike Modbus,
        OPC UA has a real quality channel for this — a client that checks the
        status code (as any serious SCADA package does) sees a fault, not a
        plausible-looking frozen reading. Values come back Good on their own
        once the bus reconnects and rebuild() republishes them. See FF-03."""
        if not self._started or self._table is None:
            return
        bad = ua.StatusCode(ua.StatusCodes.BadCommunicationError)
        for tag in self._table:
            node = self._nodes.get(tag.id)
            if node is None:
                continue
            # Same guard push() uses: writing a controller-owned node fires
            # our own subscription, and without this it would be read back
            # as a client write and queued straight onto the bus.
            self._applying.add(tag.id)
            try:
                await node.write_value(ua.DataValue(
                    ua.Variant(self._table.visible(tag.id), _VARIANT[tag.type]), bad))
            except Exception as exc:
                log.debug("could not mark %s bad quality: %s", tag.id, exc)
            finally:
                self._applying.discard(tag.id)

    def _queue_write(self, tag_id: str, value) -> None:
        if tag_id in self._applying:
            return          # our own update echoing back
        tag = self._table.get(tag_id) if self._table else None
        if tag is None:
            return
        try:
            coerced = tag.coerce(value)
        except Exception:
            log.warning("client wrote %r to %s, which is not a %s",
                        value, tag_id, tag.type)
            return
        asyncio.get_event_loop().create_task(self.bus.write(tag_id, coerced))

    async def _report(self, level: str, code: str, message: str) -> None:
        log.log({"info": logging.INFO, "warn": logging.WARNING}.get(level, logging.ERROR),
                "%s", message)
        try:
            await self.bus.status(level, code, message)
        except Exception:
            log.debug("could not report status upstream", exc_info=True)
