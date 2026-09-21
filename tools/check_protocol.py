"""Connect to a running engine and check its wire behaviour against docs/tag-bus.md.

    python tools/check_protocol.py [--timeout SECONDS]

Prints "RESULT OK" and exits 0 if `hello`, `describe`, and `update` each carry
exactly the fields the spec names -- no more, no less -- and the engine answers
every case in `engine/fixtures/server_cases.json` the way the spec says it
must. Otherwise prints "RESULT <problem>; <problem>; ..." and exits 1.

This is what would have caught FF-10 before it shipped: `hello` silently
missing `tick_ms` while the sidecar quietly filled in a default and nobody
noticed the two had drifted. Connects raw (not through TagBusClient), since the
client's own parsing would tolerate exactly the kind of drift this exists to
catch.

**The server-behaviour half is HP-19.** `engine/fixtures/tag_cases.json` pins
the two `Tag` *models* together and has done since FF-29 -- but all three
divergences HP-18 closed lived in the *servers*, which nothing compared at all.
`engine/fixtures/server_cases.json` is the shared fixture for that, and this
module is the runner for it: `tests/test_tag_parity.py` imports
`run_server_cases` and points it at `harness/engine_stub.py`, and this CLI
points the identical cases at the C# engine. Two servers, one file, one runner.

The fixture's lifecycle half — the greeting, what a second sidecar is told, a
reconnect, and what a stale or malformed `epoch` gets — is choreography between
two connections rather than a burst of frames down one, so those cases name a
`scenario` and are run by `_SCENARIOS` below rather than by `_run_case`.

Cases name tags by *role* (`@bit_output`, `@float_input`, ...) resolved from the
engine's own describe, so the file runs against any scene -- and a case whose
roles the scene does not declare is reported as skipped rather than silently
passed. That matters: the sorting scene declares no float tag at all, so the
float cases only mean something against a scene that has one, e.g.

    godot --headless --path engine -- --scene=res://templates/tank_level_control.json \\
        --bus-port=7490 --duration=60
    python tools/check_protocol.py --url ws://127.0.0.1:7490/tagbus
"""
from __future__ import annotations

import os
import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets

HELLO_FIELDS = {"t", "protocol", "engine", "tick_ms"}
DESCRIBE_FIELDS = {"t", "scene", "epoch", "tags"}
TAG_REQUIRED_FIELDS = {"id", "name", "type", "kind", "value"}
TAG_OPTIONAL_FIELDS = {"forced"}
UPDATE_FIELDS = {"t", "tick", "values"}

SERVER_CASES = (Path(__file__).resolve().parent.parent
                / "engine" / "fixtures" / "server_cases.json")

#: A tag id no scene will ever declare. A write naming it draws an
#: `unknown_tags` warning from either engine, and that reply is a round trip:
#: seeing it come back proves the connection is still open *and* still
#: processing messages. Gotcha 2 -- this is why nothing here sleeps for a guess
#: at how long an engine might take.
PROBE_ID = "__protocol_probe__.nonexistent"


async def check(url: str, timeout: float) -> list[str]:
    problems: list[str] = []
    async with websockets.connect(url, max_queue=64) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if hello.get("t") != "hello":
            problems.append(f"first message was {hello.get('t')!r}, not 'hello'")
        elif set(hello) != HELLO_FIELDS:
            problems.append(f"hello fields {sorted(hello)} != {sorted(HELLO_FIELDS)}")

        describe = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if describe.get("t") != "describe":
            problems.append(f"second message was {describe.get('t')!r}, not 'describe'")
        elif set(describe) != DESCRIBE_FIELDS:
            problems.append(f"describe fields {sorted(describe)} != {sorted(DESCRIBE_FIELDS)}")
        else:
            for tag in describe["tags"]:
                extra = set(tag) - TAG_REQUIRED_FIELDS - TAG_OPTIONAL_FIELDS
                missing = TAG_REQUIRED_FIELDS - set(tag)
                if extra or missing:
                    problems.append(f"tag {tag.get('id')} fields off: extra={extra} missing={missing}")
                    break

        # Checking the `update` shape means forcing a tag, which changes what a
        # live engine is showing somebody. Two rules follow from that, and
        # neither was being kept (HP-30).
        #
        # First, prefer a tag nobody has forced. A force on a running scene is
        # usually a person holding a sensor while they work something out, and
        # this tool should not be the reason it moved.
        bits = [t for t in describe.get("tags", [])
                if t["kind"] == "input" and t["type"] == "bit"]
        target = next((t for t in bits if not t.get("forced")), bits[0] if bits else None)

        if target is None:
            problems.append("no bit-typed input tag to force -- cannot check `update` shape")
        else:
            # Second, put back exactly what was there. Releasing unconditionally
            # is not the same thing: on an already-forced tag it would destroy a
            # deliberate override, which is the failure this is meant to avoid
            # rather than a smaller version of it. `describe` reports the
            # *visible* value, so for a forced tag that is the pinned value to
            # re-apply.
            was_forced = bool(target.get("forced"))
            was_value = target["value"]
            try:
                await ws.send(json.dumps({
                    "t": "force", "epoch": describe["epoch"],
                    "values": {target["id"]: not target["value"]},
                }))
                update = await _next_update(ws, timeout)
                if update is None:
                    problems.append("forcing an input tag produced no `update` message")
                elif set(update) != UPDATE_FIELDS:
                    problems.append(f"update fields {sorted(update)} != {sorted(UPDATE_FIELDS)}")
            finally:
                # In a finally, so a timeout or a failed assertion above does not
                # leave the engine pinned. A checker that reports a problem and
                # also leaves a sensor stuck has cost more than it found.
                restore = {"t": "force", "epoch": describe["epoch"]}
                if was_forced:
                    restore["values"] = {target["id"]: was_value}
                else:
                    restore["clear"] = [target["id"]]
                await ws.send(json.dumps(restore))

                # Wait for the engine to tell us it took effect before leaving
                # the `async with` and closing the socket. Sending is not the
                # same as landing: the engine drains its socket from _Process,
                # and a frame that arrives in the same breath as the close can
                # go unread -- which would leave the restore as decorative as
                # the missing release it replaces.
                await _next_update(ws, min(timeout, 2.0), target["id"])

    return problems


async def _next_update(ws, timeout: float, tag_id: str | None = None) -> dict | None:
    """The next `update`, or the next one carrying `tag_id`. None on timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), max(remaining, 0.01)))
        except asyncio.TimeoutError:
            return None
        if msg.get("t") != "update":
            continue
        if tag_id is None or tag_id in msg.get("values", {}):
            return msg
    return None


# --- server behaviour, from the shared fixture (HP-19) ----------------------


def load_server_cases(path: Path | str = SERVER_CASES) -> list[dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return doc["cases"]


def resolve_roles(tags: list[dict]) -> dict[str, str]:
    """`bit_output` -> the first bit-typed output tag in the describe, etc.

    First wins rather than, say, alphabetically first, so the role a case gets
    is stable for a given scene and reads the same in a failure message twice
    running.
    """
    roles: dict[str, str] = {}
    for tag in tags:
        roles.setdefault(f"{tag['type']}_{tag['kind']}", tag["id"])
    return roles


def _role_id(name: str, roles: dict[str, str]) -> str:
    """`@bit_output` -> the resolved tag id; anything else passes through, so a
    case can also name a literal id (the unknown-tag case does)."""
    return roles[name[1:]] if name.startswith("@") else name


def _sub_map(values: dict, roles: dict[str, str]) -> dict:
    return {_role_id(k, roles): v for k, v in values.items()}


def _sub_text(raw: str, roles: dict[str, str], epoch: int) -> str:
    out = raw.replace("@epoch", str(epoch))
    # Longest first: no role is a prefix of another today, and relying on that
    # is the kind of thing that stops being true quietly.
    for role in sorted(roles, key=len, reverse=True):
        out = out.replace(f"@{role}", roles[role])
    return out


async def _greet(ws, timeout: float) -> tuple[dict, dict]:
    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
    if msg.get("t") == "status":          # e.g. "already connected"
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
    describe = json.loads(await asyncio.wait_for(ws.recv(), timeout))
    return msg, describe


async def _collect(ws, deadline: float, done) -> tuple[list[dict], dict, list[str], bool]:
    """Read frames until *done* says we have what we came for, or *deadline*.

    Returns the statuses this case produced, the forced values and released ids
    seen on the observe channel, and whether the liveness probe came back.
    """
    loop = asyncio.get_event_loop()
    statuses: list[dict] = []
    forced: dict = {}
    cleared: list[str] = []
    alive = False
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        except (asyncio.TimeoutError, websockets.exceptions.WebSocketException):
            break
        if msg.get("t") == "status":
            if msg.get("code") == "unknown_tags" and PROBE_ID in str(msg.get("message")):
                alive = True
            else:
                statuses.append(msg)
        elif msg.get("t") == "observe":
            forced.update(msg.get("forced", {}))
            cleared.extend(msg.get("cleared", []))
        if alive and done(statuses, forced, cleared):
            break
    return statuses, forced, cleared, alive


async def _burst(ws, frames: list[str], epoch: int, timeout: float,
                 settle: float, done) -> tuple[list[dict], dict, list[str], bool]:
    """Send *frames*, then the liveness probe, then read until *done*.

    The probe is what makes this event-driven rather than timed: the engine's
    reply to it is a round trip, so an engine that is still there answers and
    an engine that dropped the connection does not.
    """
    for frame in frames:
        await ws.send(frame)
    await ws.send(json.dumps({"t": "write", "epoch": epoch,
                              "values": {PROBE_ID: True}}))
    return await _collect(ws, asyncio.get_event_loop().time() + settle, done)


async def _read_table(url: str, timeout: float) -> tuple[dict, int, dict[str, dict]]:
    """Reconnect and read the engine's own description of its table.

    The only read-back the protocol offers for an `output` tag: `update` is
    input-only by design, so "did that write land?" is answered by asking the
    engine to describe itself again.
    """
    async with websockets.connect(url, max_queue=64) as ws:
        _, describe = await _greet(ws, timeout)
    return describe, describe["epoch"], {t["id"]: t for t in describe["tags"]}


async def _restore(url: str, timeout: float, values: dict, release: list[str]) -> None:
    """Put the scene back. A checker that reports a problem and also leaves the
    belt running has cost more than it found."""
    if not values and not release:
        return
    async with websockets.connect(url, max_queue=64) as ws:
        _, describe = await _greet(ws, timeout)
        if release:
            await ws.send(json.dumps(
                {"t": "force", "epoch": describe["epoch"], "clear": release}))
        if values:
            await ws.send(json.dumps(
                {"t": "write", "epoch": describe["epoch"], "values": values}))
        # Same reason as the restore in check(): sending is not landing, and a
        # frame arriving in the same breath as the close can go unread.
        await ws.send(json.dumps(
            {"t": "write", "epoch": describe["epoch"], "values": {PROBE_ID: True}}))
        await _collect(ws, asyncio.get_event_loop().time() + min(timeout, 2.0),
                       lambda *_: True)


async def run_server_cases(url: str, timeout: float = 10.0,
                           cases: list[dict] | None = None,
                           settle: float = 2.0) -> tuple[list[str], list[str]]:
    """Drive every case in the shared fixture against the engine at *url*.

    Returns (problems, skipped). Skipped names the cases whose roles this
    scene does not declare -- reported rather than swallowed, because a fixture
    that quietly runs nothing is the failure mode HP-19 exists to prevent.
    """
    cases = cases if cases is not None else load_server_cases()
    problems: list[str] = []
    skipped: list[str] = []

    _, _, initial = await _read_table(url, timeout)
    roles = resolve_roles(list(initial.values()))

    for case in cases:
        missing = [r for r in case.get("needs", []) if r not in roles]
        if missing:
            skipped.append(f"{case['name']} (no {', '.join(missing)} in this scene)")
            continue
        # Most cases are one connection and a few frames, and say so entirely in
        # the fixture. The lifecycle ones are choreography between two
        # connections -- who gets refused, what a reconnect is told, which epoch
        # a frame is stamped with -- so they name a `scenario` here instead.
        run = _SCENARIOS.get(case.get("scenario"), _run_case)
        problems += await run(url, case, roles, initial, timeout, settle)
    return problems, skipped


async def _run_case(url: str, case: dict, roles: dict[str, str],
                    initial: dict[str, dict], timeout: float,
                    settle: float) -> list[str]:
    name = case["name"]
    problems: list[str] = []
    want_status = list(case.get("expect_status", []))
    want_observe = _sub_map(case.get("expect_observe", {}), roles)
    want_cleared = [_role_id(r, roles) for r in case.get("expect_cleared", [])]

    def done(statuses, forced, cleared) -> bool:
        codes = [s.get("code") for s in statuses]
        return (all(c in codes for c in want_status)
                and all(k in forced for k in want_observe)
                and all(c in cleared for c in want_cleared))

    touched: set[str] = set()
    for key in ("prepare", "write", "force", "expect_values"):
        touched |= {_role_id(k, roles) for k in case.get(key, {})}
    forced_here = {_role_id(k, roles) for k in case.get("force", {})}
    forced_here |= {tid for tid in touched if f'"{tid}"' in case.get("raw", "")
                    and '"t":"force"' in case.get("raw", "")}

    # A force this case applies has to be *seen* before the case releases it.
    # Both channels are delta-only, so a force applied and released between two
    # engine ticks nets out to nothing at all -- correct behaviour, and a
    # release the case never sees. Waiting for the observe puts a tick between
    # them without anybody sleeping for a guess at how long one takes.
    want_forced = set(_sub_map(case.get("force", {}), roles))

    def applied(statuses, forced, cleared) -> bool:
        codes = [s.get("code") for s in statuses]
        return (all(c in codes for c in want_status)
                and all(k in forced for k in want_observe)
                and all(k in forced for k in want_forced))

    try:
        async with websockets.connect(url, max_queue=64) as ws:
            _, describe = await _greet(ws, timeout)
            epoch = describe["epoch"]

            frames: list[str] = []
            if "prepare" in case:
                frames.append(json.dumps({"t": "write", "epoch": epoch,
                                          "values": _sub_map(case["prepare"], roles)}))
            if "write" in case:
                frames.append(json.dumps({"t": "write", "epoch": epoch,
                                          "values": _sub_map(case["write"], roles)}))
            if "raw" in case:
                frames.append(_sub_text(case["raw"], roles, epoch))
            if "force" in case:
                frames.append(json.dumps({"t": "force", "epoch": epoch,
                                          "values": _sub_map(case["force"], roles)}))
            statuses, forced, cleared, alive = await _burst(
                ws, frames, epoch, timeout, settle, applied)

            # A release goes in a second burst, after the first has been
            # answered. Both channels are delta-only, so a force applied and
            # released between two ticks correctly produces nothing at all --
            # asking for the release in the same breath as the force would be
            # asserting against the spec rather than for it.
            if "clear" in case:
                more = await _burst(
                    ws, [json.dumps({"t": "force", "epoch": epoch,
                                     "clear": [_role_id(r, roles)
                                               for r in case["clear"]]})],
                    epoch, timeout, settle,
                    lambda s, f, c: all(x in c for x in want_cleared))
                statuses += more[0]
                forced.update(more[1])
                cleared += more[2]
                alive = alive and more[3]
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        return [f"{name}: the connection failed: {exc}"]

    if not alive:
        problems.append(
            f"{name}: the engine stopped answering -- it dropped the connection "
            f"or stopped reading it (statuses seen: "
            f"{[s.get('code') for s in statuses]})")

    codes = [s.get("code") for s in statuses]
    for want in want_status:
        if want not in codes:
            problems.append(f"{name}: expected a {want!r} status, got {codes or 'nothing'}")
    for banned in case.get("forbid_status", []):
        if banned in codes:
            problems.append(f"{name}: unexpected {banned!r} status: "
                            f"{[s.get('message') for s in statuses if s.get('code') == banned]}")
    for tag_id, want in want_observe.items():
        if forced.get(tag_id, _MISSING) != want:
            problems.append(f"{name}: expected observe to report {tag_id}={want!r}, "
                            f"got {forced.get(tag_id, 'nothing')!r}")
    for tag_id in want_cleared:
        if tag_id not in cleared:
            problems.append(f"{name}: expected observe to release {tag_id}, got {cleared}")

    if "expect_values" in case or "expect_forced" in case:
        try:
            _, _, table = await _read_table(url, timeout)
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as exc:
            problems.append(f"{name}: could not read the table back: {exc}")
            table = {}
        for role, want in case.get("expect_values", {}).items():
            tag_id = _role_id(role, roles)
            got = table.get(tag_id, {}).get("value", _MISSING)
            if not _same(got, want):
                problems.append(f"{name}: {tag_id} should read {want!r}, reads {got!r}")
        if "expect_forced" in case:
            should_be = {_role_id(r, roles) for r in case["expect_forced"]}
            for tag_id in sorted(touched):
                is_forced = bool(table.get(tag_id, {}).get("forced"))
                if is_forced != (tag_id in should_be):
                    problems.append(
                        f"{name}: {tag_id} forced={is_forced}, expected "
                        f"{tag_id in should_be}")

    await _restore(url, timeout,
                   {tid: initial[tid]["value"] for tid in sorted(touched)
                    if tid in initial and initial[tid]["kind"] == "output"},
                   sorted(forced_here))
    return problems


class _Missing:
    def __repr__(self) -> str:
        return "nothing"


_MISSING = _Missing()


def _same(got, want) -> bool:
    if isinstance(want, float) or isinstance(got, float):
        try:
            return abs(float(got) - float(want)) <= 1e-9
        except (TypeError, ValueError):
            return False
    return got == want


# --- lifecycle scenarios (HP-19, the half the last round deferred) ----------
#
# The first round of this fixture drew its boundary deliberately: the three
# HP-18 divergences, force state, the non-finite values, and the two answers an
# engine can give about a value it will not take -- and it left out lifecycle,
# reconnect, epoch semantics and second-client rejection, because HP-31 and
# HP-33 were about to reshape exactly those orderings and pinning them first
# would have pinned the bug. Both have landed. These are the cases that were
# waiting for them.
#
# They are choreography between two connections rather than a burst of frames
# down one, so each names a `scenario` in the fixture instead of describing
# itself in `write`/`force`/`expect_*` keys.


async def _frames_until_closed(ws, seconds: float) -> tuple[list[dict], bool]:
    """Everything *ws* says until the far end closes it, or *seconds* pass."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    frames: list[dict] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return frames, False
        try:
            frames.append(json.loads(await asyncio.wait_for(ws.recv(), remaining)))
        except asyncio.TimeoutError:
            return frames, False
        except websockets.exceptions.ConnectionClosed:
            return frames, True
        except (ValueError, websockets.exceptions.WebSocketException):
            return frames, False


def _drop_without_closing(ws) -> None:
    """Yank the socket out from under the engine: no close frame, no goodbye.

    This is what a crashed sidecar looks like from the engine's seat, and it is
    a different event from the orderly close every other case performs.
    """
    ws.transport.abort()


async def _greeting(url: str, case: dict, roles, initial, timeout: float,
                    settle: float) -> list[str]:
    """`hello` first, `describe` second, and each carrying exactly its fields.

    `check()` above has always asserted this, but only from the CLI and only
    against whichever engine the CLI was pointed at. Here it is a fixture case,
    so the Python engine answers it in pytest too -- which is the whole point of
    the file.
    """
    name = case["name"]
    problems: list[str] = []
    async with websockets.connect(url, max_queue=64) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if hello.get("t") != "hello":
            return [f"{name}: the first frame was {hello.get('t')!r}, not 'hello'"]
        if set(hello) != HELLO_FIELDS:
            problems.append(f"{name}: hello fields {sorted(hello)} != {sorted(HELLO_FIELDS)}")
        if hello.get("protocol") != 0:
            problems.append(f"{name}: hello says protocol {hello.get('protocol')!r}, not 0")
        tick = hello.get("tick_ms")
        if not isinstance(tick, int) or isinstance(tick, bool) or tick <= 0:
            problems.append(f"{name}: hello says tick_ms {tick!r}, which is not a tick")

        describe = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if describe.get("t") != "describe":
            return problems + [f"{name}: the second frame was {describe.get('t')!r}, "
                               "not 'describe'"]
        if set(describe) != DESCRIBE_FIELDS:
            problems.append(f"{name}: describe fields {sorted(describe)} != "
                            f"{sorted(DESCRIBE_FIELDS)}")
        epoch = describe.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            problems.append(f"{name}: the first describe carries epoch {epoch!r}; "
                            "an epoch is a counter and starts at 1")
        for tag in describe.get("tags", []):
            extra = set(tag) - TAG_REQUIRED_FIELDS - TAG_OPTIONAL_FIELDS
            missing = TAG_REQUIRED_FIELDS - set(tag)
            if extra or missing:
                problems.append(f"{name}: tag {tag.get('id')} fields off: "
                                f"extra={extra} missing={missing}")
                break
    return problems


async def _second_client(url: str, case: dict, roles, initial, timeout: float,
                         settle: float) -> list[str]:
    """What the *second* sidecar is told, and that the first one keeps its seat.

    Only one sidecar may be connected: two drivers writing the same output tag
    is a bug, not a feature. But "refused" has to mean the same thing on both
    engines. The C# server used to tear the TCP connection down before the
    websocket handshake finished, so the second sidecar got "did not receive a
    valid HTTP response" and no reason at all, while engine_stub answered with
    a status naming it.
    """
    name = case["name"]
    problems: list[str] = []
    async with websockets.connect(url, max_queue=64) as first:
        hello, describe = await _greet(first, timeout)
        epoch = describe["epoch"]

        try:
            second = await websockets.connect(url, max_queue=64, open_timeout=timeout)
        except (OSError, asyncio.TimeoutError,
                websockets.exceptions.WebSocketException) as exc:
            problems.append(
                f"{name}: the second connection was refused at the socket "
                f"({type(exc).__name__}: {exc}) -- it must be accepted and "
                f"answered with a status saying why, or the sidecar cannot tell "
                f"'another driver has it' from 'the engine is not there'")
            second = None

        if second is not None:
            try:
                frames, closed = await _frames_until_closed(second, settle)
            finally:
                await second.close()
            if not frames:
                problems.append(f"{name}: the second sidecar was told nothing at all")
            else:
                answer = frames[0]
                if answer.get("t") != "status":
                    problems.append(f"{name}: the second sidecar was answered with "
                                    f"{answer.get('t')!r}, not a status")
                else:
                    if answer.get("code") != "already_connected":
                        problems.append(f"{name}: the refusal carries code "
                                        f"{answer.get('code')!r}, not 'already_connected'")
                    if answer.get("level") != "error":
                        problems.append(f"{name}: the refusal is level "
                                        f"{answer.get('level')!r}, not 'error'")
            seen = [f.get("t") for f in frames]
            if "describe" in seen or "hello" in seen:
                problems.append(f"{name}: a refused sidecar was sent {seen} -- it must "
                                f"not be given the tag set or an epoch to write against")
            if not closed:
                problems.append(f"{name}: the engine left the refused connection open")

        # And the sidecar that was there first is untouched by any of it.
        _, _, _, alive = await _burst(first, [], epoch, timeout, settle,
                                      lambda *_: True)
        if not alive:
            problems.append(f"{name}: refusing a second sidecar cost the first one "
                            f"its session")
    return problems


async def _abrupt_reconnect(url: str, case: dict, roles, initial, timeout: float,
                            settle: float) -> list[str]:
    """A sidecar that vanished must not lock the next one out.

    The orderly close every other case performs is the easy half: the engine is
    told. A crash is not, and the engine's idea of who is connected is only ever
    as fresh as its last poll -- so a reconnect arriving in the same breath as
    the drop was refused as a second sidecar, at the socket, with no status and
    no explanation. Measured before the fix: 15 of 15.
    """
    name = case["name"]
    problems: list[str] = []
    ws = await websockets.connect(url, max_queue=64)
    try:
        await _greet(ws, timeout)
    finally:
        _drop_without_closing(ws)

    try:
        async with websockets.connect(url, max_queue=64, open_timeout=timeout) as ws2:
            first = json.loads(await asyncio.wait_for(ws2.recv(), timeout))
            if first.get("t") != "hello":
                problems.append(
                    f"{name}: the reconnect was answered with {first.get('t')!r}"
                    f"/{first.get('code')!r} rather than a fresh hello")
            else:
                describe = json.loads(await asyncio.wait_for(ws2.recv(), timeout))
                if describe.get("t") != "describe":
                    problems.append(f"{name}: the reconnect was not re-described "
                                    f"({describe.get('t')!r} followed hello)")
    except (OSError, asyncio.TimeoutError,
            websockets.exceptions.WebSocketException) as exc:
        problems.append(
            f"{name}: the engine refused the reconnect ({type(exc).__name__}: {exc}) "
            f"-- a sidecar that died without closing still holds the seat")
    return problems


async def _reconnect(url: str, case: dict, roles, initial, timeout: float,
                     settle: float) -> list[str]:
    """Reconnecting re-describes: same tags, a fresh epoch, the values kept.

    The engine is the authority and a sidecar coming back has to be handed the
    truth, not a blank table: whatever the last controller wrote is still what
    the machine is doing.
    """
    name = case["name"]
    problems: list[str] = []
    target = roles["bit_output"]
    want = not bool(initial[target]["value"])

    async with websockets.connect(url, max_queue=64) as ws:
        _, first = await _greet(ws, timeout)
        epoch = first["epoch"]
        ids = [t["id"] for t in first["tags"]]
        await _burst(ws, [json.dumps({"t": "write", "epoch": epoch,
                                      "values": {target: want}})],
                     epoch, timeout, settle, lambda *_: True)

    async with websockets.connect(url, max_queue=64) as ws:
        hello, again = await _greet(ws, timeout)
        if hello.get("t") != "hello":
            problems.append(f"{name}: the reconnect opened with {hello.get('t')!r}, "
                            f"not a fresh hello")
        if again["epoch"] <= epoch:
            problems.append(f"{name}: the reconnect was described with epoch "
                            f"{again['epoch']}, which does not follow {epoch} -- "
                            f"every describe bumps it")
        if [t["id"] for t in again["tags"]] != ids:
            problems.append(f"{name}: the tag set changed across a reconnect")
        table = {t["id"]: t for t in again["tags"]}
        got = table.get(target, {}).get("value", _MISSING)
        if not _same(got, want):
            problems.append(f"{name}: {target} reads {got!r} after reconnecting, "
                            f"not the {want!r} the previous session wrote")

    await _restore(url, timeout, {target: initial[target]["value"]}, [])
    return problems


async def _stale_epoch(url: str, case: dict, roles, initial, timeout: float,
                       settle: float) -> list[str]:
    """A frame stamped with the epoch from before the last describe.

    A reconnect is a re-describe, and a re-describe is a rebuild as far as the
    epoch is concerned -- so this drives the real thing over the wire rather
    than simulating it. The same frame is then sent again with the epoch this
    connection was actually given: without that half, a case would pass just as
    happily against an engine that had stopped accepting anything at all.
    """
    name = case["name"]
    kind = case.get("frame", "write")
    problems: list[str] = []
    target = roles["bit_output"]
    base = bool(initial[target]["value"])
    other = not base

    async with websockets.connect(url, max_queue=64) as ws:
        _, first = await _greet(ws, timeout)
        old = first["epoch"]
        await _burst(ws, [json.dumps({"t": "write", "epoch": old,
                                      "values": {target: base}})],
                     old, timeout, settle, lambda *_: True)

    async with websockets.connect(url, max_queue=64) as ws:
        _, again = await _greet(ws, timeout)
        new = again["epoch"]
        if new <= old:
            problems.append(f"{name}: the epoch went {old} -> {new} across a "
                            f"describe; it has to advance")
        _, forced, _, alive = await _burst(
            ws, [json.dumps({"t": kind, "epoch": old, "values": {target: other}})],
            new, timeout, settle, lambda *_: True)
        if not alive:
            problems.append(f"{name}: the engine stopped answering after a {kind} "
                            f"carrying a stale epoch")
        if target in forced:
            problems.append(f"{name}: a {kind} carrying a stale epoch was reported on "
                            f"the observe channel as {target}={forced[target]!r}")

    _, _, table = await _read_table(url, timeout)
    got = table.get(target, {}).get("value", _MISSING)
    if not _same(got, base):
        problems.append(f"{name}: {target} reads {got!r} -- a {kind} stamped with the "
                        f"epoch from before the last describe landed anyway")
    if table.get(target, {}).get("forced"):
        problems.append(f"{name}: {target} is pinned -- a {kind} stamped with the "
                        f"epoch from before the last describe landed anyway")

    async with websockets.connect(url, max_queue=64) as ws:
        _, now = await _greet(ws, timeout)
        current = now["epoch"]
        await _burst(ws, [json.dumps({"t": kind, "epoch": current,
                                      "values": {target: other}})],
                     current, timeout, settle, lambda *_: True)
    _, _, table = await _read_table(url, timeout)
    got = table.get(target, {}).get("value", _MISSING)
    if not _same(got, other):
        problems.append(f"{name}: the same {kind} stamped with the current epoch did "
                        f"not land either ({target} reads {got!r}), so the engine is "
                        f"refusing everything and the case above proves nothing")

    await _restore(url, timeout, {target: initial[target]["value"]},
                   [target] if kind == "force" else [])
    return problems


#: `scenario` in the fixture -> the runner for it. A case without one is the
#: ordinary declarative kind and goes to `_run_case`.
_SCENARIOS = {
    "greeting": _greeting,
    "second_client": _second_client,
    "abrupt_reconnect": _abrupt_reconnect,
    "reconnect": _reconnect,
    "stale_epoch": _stale_epoch,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",
                        default=os.environ.get("FF_BUS_URL",
                                               "ws://127.0.0.1:7411/tagbus"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--shape-only", action="store_true",
                        help="skip the server-behaviour fixture, check message shapes only")
    args = parser.parse_args()

    async def everything() -> tuple[list[str], list[str]]:
        problems = await check(args.url, args.timeout)
        skipped: list[str] = []
        if not args.shape_only:
            more, skipped = await run_server_cases(args.url, args.timeout)
            problems += more
        return problems, skipped

    try:
        problems, skipped = asyncio.run(everything())
    except (OSError, asyncio.TimeoutError) as exc:
        print(f"RESULT could not reach {args.url}: {exc}")
        return 1

    for name in skipped:
        print(f"SKIP {name}")
    if problems:
        print("RESULT " + "; ".join(problems))
        return 1
    print("RESULT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
