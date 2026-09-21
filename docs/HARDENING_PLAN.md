# FactoryForge — The Things That Break Before Anyone Sees Them

**Status:** HP-01 … HP-56. 25 done (the release gate bar HP-09), 31 open.
**Revised:** 2026-09-20, after a second Codex pass over the first draft (Appendix C).
**Started:** 2026-09-20, against `f597e27`.

Every *feature* plan document in `docs/` is closed. Forty-six UX items, nine components,
five line primitives, six build-flow items, the shop, the selection, the names,
the tag inspector, the briefs — all done, all checked off. (`ROADMAP.md:57–58`
still carries unchecked M1 items, and FF-08 is still open by
`.github/workflows/test-plan.yml:13–15`; those are the exceptions.) The build is
clean at zero warnings, 73 of 73 Python tests pass, twenty-nine engine
self-tests exist, CI is green on `master`, and the two test-plan sections CI
does not run (`F`, the engine/sidecar seam, and `G`, robustness) pass 12 of 12
locally.

So the natural reading is that the project is finished and the next thing is
more parts.

It is not, and this document is the argument for why. Three findings set the
shape of it:

**Nothing has ever shipped.** `gh api repos/.../releases` returns `0`. There are
no git tags. `release.yml` triggers on `v*` and has only ever run as a
`workflow_dispatch` dry run, three times, last on 2026-08-24. Meanwhile
`README.md:103` opens with *"Just want to run it? Download a release"* and links
to an empty Releases page. The 92 MB `dist/FactoryForge-windows.zip` exists on
exactly one machine in the world. Every claim in `PACKAGING.md` about a verified,
gated, two-platform build is true, and none of it has reached a user.

**The editor silently destroys work.** Saving to an unwritable path prints
"Saved scene" and clears the dirty flag. Opening a corrupt scene file wipes the
scene you had before it tries to parse the new one. Deleting a tuned conveyor
and pressing Ctrl+Z — which the console itself advertises, *"Ctrl+Z to put them
back"* — brings the part back at factory defaults. Moving a part with `M` and
undoing deletes it outright. None of these are exotic; all four are on the first
hour of a student's path, and all four are invisible at the moment they happen.

**The part library has started to drift, which is the PRD's own anti-goal.**
`docs/PRD.md:110` lists "a part library only the maintainer can extend" as a
sign the project drifted. `docs/PART_AUTHORING.md:45–53` already names the cost
honestly, in a table of **eight** integration points a new part must touch
outside its own class — `PartCatalog.All`, `SceneEditor.CreatePartNode`,
`PlacedPart.TagSuffixesByType`, `PartTagManager.RegisterPartTags`,
`SceneEditor._PhysicsProcess`, `PartProperties.Capture`/`Apply`,
`PartPropertyInspectorUI.InspectNode` and optionally `WholeBodyOperableTag`.
That table is a good piece of writing and the wrong artefact: a checklist of
eight places to remember is a design problem documented rather than solved. The
drift has already happened: the weighing conveyor
registers a `.fault` tag that nothing dispatches, so forcing it does nothing,
unlike every other conveyor. Two outside part contributions would conflict in
the same shared files.

The sources are three reviews run on 2026-09-20 against `f597e27`: a Codex
read-only pass that produced forty-two findings, a separate pass here, and a
second Codex pass over the first draft of this document. Where a finding below
carries *Codex #n*, that is its number in the first Codex report; six of those
were re-verified here by reading the code and are marked **confirmed**.

The second Codex pass corrected six claims in the first draft, four of which
were mine. They are recorded in Appendix C rather than quietly fixed, because
two of them were severity arguments built on assumptions that had not been
checked — which is the same failure this document criticises elsewhere.

---

## How to read a work item

```
HP-nn — one-line title
  Files:      what gets touched
  Done when:  the observable state that means it is finished
  Verify:     the command or the click that proves it
  Size:       S | M | L | XL
```

**Sizes.** `S` = under half a day. `M` = one to two days. `L` = three to five
days. `XL` = more than a week. Sizes assume familiarity with this codebase.

**Phases are thematic, not a priority order.** Priority is the **release gate**
in the Sequencing section — the list of items that must land before HP-09,
drawn from every phase. Read that list first; the phase headings are there to
group related work, not to rank it.

---

## Phase 0 — The editor loses work, silently

Four of these destroy something a person made and give no sign. One deletes a
part you were only trying to move. HP-08 is what makes the rest durable: the
tests that should have caught them cannot see them. HP-47, listed later with the
second review's findings, belongs to this phase too.

This phase is not about polish. A student who loses an afternoon's scene to a
save that said it worked does not file a bug, they stop using the tool.

It is also not the whole of the pre-release work — see the release gate under
Sequencing, which pulls eleven more items forward from later phases on exactly
this criterion.

**HP-01 — A failed save must not report success**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* a save reports success only after a write that completed. That
covers `Open` returning null *and* a write that fails or truncates after a
successful open — the second is the one a disk-full or a yanked USB stick
produces. On any failure `IsDirty` stays true and the user is told.
*Verify:* save to a path under a read-only directory; the UI says it failed and
the title still shows unsaved changes.
*Size:* S. *Codex #6* — **confirmed**: `SceneEditor.cs:2351` opens the file,
`:2352` is `file?.StoreString(json)`, and `:2353`–`:2354` set `IsDirty = false`
and print `Saved scene to {path}` unconditionally.
*See also:* HP-47, which is the durable half of this.

**HP-02 — Validate a scene file before destroying the open one**
*Files:* `engine/src/Editor/SceneEditor.cs`, `engine/src/Editor/SceneData.cs`.
*Done when:* `LoadSceneFromFile` opens, reads, deserializes **and structurally
validates** first, and clears the current scene only once it holds a scene it
can build. A malformed file leaves the open scene untouched and says why.
Validation covers the three ways this currently fails, not just the first:
`SceneData.FromJson` throwing, it returning null, and a part whose
position/rotation arrays are too short to construct.
*Verify:* HP-08's rewritten G1 — open a scene, then load
`testplan_corrupt.json`; the original scene is still there.
*Size:* **M**, revised up from S. *Codex #5* — **confirmed**:
`SceneEditor.cs:2365` calls `ClearAllPlacedParts()` before `FileAccess.Open`.
But "parse first" alone is not enough, and the first draft of this item said it
was: `SceneData.cs:37` calls `JsonSerializer.Deserialize<SceneData>(json)` with
no try/catch, so malformed JSON *throws* rather than returning null, and short
arrays throw later still at `SceneEditor.cs:2458`
(`new Vector3(p.Position[0], p.Position[1], p.Position[2])`). The item needs a
validation boundary, and an unknown-part-type policy, not a reordering.
*Shares a boundary with:* HP-07 and HP-15 — all three must reject before the
destructive clear at `:2365`. Build the boundary once.

**HP-03 — Undoing a delete must restore the part's settings**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* `PartCommand` captures a full `PartInstanceData` on delete and
respawns through `SpawnFromData`, not `SpawnPart`.
*Verify:* HP-08's new self-test; by hand, set a conveyor to 0.2 m/s, delete it,
Ctrl+Z, and read the speed back.
*Size:* S. *Codex #3* — **confirmed**: `PartCommand` (`SceneEditor.cs:539`)
stores type, position, rotation and id, and its `Respawn()` at `:577` calls
`SpawnPart`, which never calls `PartProperties.Apply`. `SpawnFromData`
(`SceneEditor.cs:2460`) does. The machinery to fix it already exists and is what
`DuplicateCommand` uses.

Scope, corrected from the first draft of this document: the affected path is
`DeleteSelectedPart` at `:2120`, which covers both a single selection and a
group, since a single selection is a one-element selection. `:2907` is *not* a
second delete path — it is the placement commit, `isPlacement: true` at `:2910`.
Clearing a scene is separate again (`ClearSceneCommand`, `:2729`) and should be
checked but is not this item.

**HP-04 — `M`-move must undo as a move, not as a placement**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* committing a move records a `MoveCommand` (or equivalent) whose
undo returns the part to its origin, with its settings and identity intact.
*Verify:* place a tuned part, `M` it two cells over, Ctrl+Z; it is back where it
started and still tuned.
*Size:* M. *Codex #2*: the commit forgets the original and records only a
placement at the destination (`SceneEditor.cs:755`, `:2895`), so undo removes
the placement and restores nothing. It also inherits HP-03's settings loss.

**HP-05 — Command history must not hold freed nodes**
*Files:* `engine/src/Editor/SceneEditor.cs`,
`engine/src/Editor/EditorCommandHistory.cs`.
*Done when:* move and rotate commands resolve their target the same way
placement and deletion do, so a command that outlives its node's destruction
finds the replacement rather than the corpse. `Undo()` throwing no longer
silently eats the history entry.
*Verify:* nudge a group, delete it, let a frame pass, Ctrl+Z twice.
*Size:* M. *Codex #1, #22*: `MoveCommand` (`SceneEditor.cs:1030`) retains a
`Node3D`; deletion undo builds a new one. `EditorCommandHistory.cs:43` pops
before invoking `Undo`.
*Depends on:* HP-37, and this is not optional. "Resolve the same way placement
and deletion do" is the wrong target, because *that* mechanism is itself
defective — `RemovePartAt` matches on type and position (`SceneEditor.cs:615`–
`:616`), which is HP-37's bug. Establish one stable command identity first, then
point HP-03, HP-04, HP-05 and HP-20 at it.

**HP-06 — Settings a part exposes must survive a save**
*Files:* `engine/src/Editor/PartProperties.cs`, and the parts named below.
*Done when:* conveyor `Direction`, emitter `DropClearance`, roller spacing and
chute width, thickness and lip offsets round-trip through a save and load.
*Verify:* `--self-test=scene`, extended to assert these keys the way it already
asserts `VariableConveyor.Speed`'s deliberate *absence*.
*Size:* M. *Codex #18*. Note the existing rule in `AGENTS.md` — a value the part
*computes* is not a setting — still holds; each of these is configuration.

**HP-07 — Read the scene-file version that is already being written**
*Files:* `engine/src/Editor/SceneData.cs`, `engine/src/Editor/SceneEditor.cs`.
*Done when:* loading a scene whose `version` is newer than this build
understands refuses with a message naming the version, instead of half-loading.
A migration hook exists for the first format change, even if it is empty.
*Verify:* hand-edit a template to `"version": "9.0"` and open it.
*Size:* S. Found here: `SceneData.Version` is written into every scene file and
read nowhere in `engine/src/` — `grep -rn "\.Version" engine/src/` returns
nothing. Section G3 proves unknown keys are ignored gracefully, which is right
for a *forward* key and wrong for a whole future format: a v2 scene will load
quietly and lose what it does not recognise. This matters from the moment
students start sharing scene files, which is the moment HP-09 ships.

**HP-08 — Make the tests able to see Phase 0**
*Files:* `tools/test_plan.py`, `engine/src/Sim/BuildFlowSelfTest.cs`,
`engine/src/Sim/SceneSelfTest.cs`, new self-test as needed.
*Done when:* G1 actually opens the corrupt file it writes; a self-test covers
delete → undo → settings and move → undo → position; and the all-parts dispatch
check asserts an effect rather than the absence of a throw.
*Verify:* per `AGENTS.md` gotcha 24 — reintroduce each Phase 0 bug deliberately
and confirm the new test fails. A new check that has only been seen to pass is a
check nobody knows the shape of.
*Size:* M for the loss regressions and the G1 fix; **L if the all-parts effects
audit is taken literally**, because `SceneSelfTest.cs:389` currently asserts
`Expect(true, "part dispatch survives every output being driven")` and replacing
that with a real per-part effect assertion across the whole catalog is a
different job from adding two undo tests. **Split it**: the regressions gate the
release, the audit does not.
*Codex #27, #24, #28* — #27 **confirmed**: `test_plan.py:491` writes
`testplan_corrupt.json` and `:492` launches the engine with `--duration=6` and no
`--scene=` argument pointing at it. G1 has never opened that file. It passes
today, and it would pass with HP-02 unfixed.
*Blocks:* HP-09 (the regression half).

---

## Phase 1 — Ship the thing the README already promises

Phase 0 first, because a release is a promise that the tool keeps your work.

**HP-09 — Publish a real release**
*Files:* none; a tag.
*Done when:* `v1.0.0` exists, `release.yml` has run for real on it, and both
platform archives are attached to a published GitHub release.
*Verify:* download the Windows archive on a machine with no Godot, no .NET SDK
and no Python; run it; open a template; connect the bundled sidecar with F5.
*Size:* S — the machinery is built and gated, it has simply never been fired.
*Depends on:* HP-08.

**HP-10 — Make the project findable**
*Files:* repository settings; `README.md` if the name changes.
*Done when:* the repository has a description and topics, and the `factory` vs
`factoryforge` name mismatch is resolved one way or the other.
*Verify:* the repo page shows what this is without opening a file.
*Size:* S. Found here: public, zero stars, no description, no topics, and a
clone URL that does not contain the product's name. For a project whose entire
thesis is being the open alternative to a €278/year tool, nobody can currently
find it.

**HP-11 — Reconcile the documentation with the code**
*Files:* `AGENTS.md`, `docs/GETTING_STARTED.md`, `docs/ROADMAP.md`, `README.md`.
*Done when:* one test count appears everywhere; `AGENTS.md` no longer says
packaging is undone; `ROADMAP.md`'s stamp reflects the work since; and
`README.md` and `GETTING_STARTED.md` agree on how a student starts.
*Verify:* `grep -rn "41 tests\|71 unit" docs/ AGENTS.md README.md` returns
nothing.
*Size:* S. Found here: the suite is 73. `AGENTS.md:69` and `:83` say 41,
`GETTING_STARTED.md:28` says 71, the README badge says 73. `AGENTS.md:445` says
packaging "is not" done while `PACKAGING.md` records it verified in CI on two
platforms. `ROADMAP.md` is stamped 2026-08-07 with six weeks of shipped work
after it. `AGENTS.md` is the file this project tells every new contributor and
every agent to read first.

The first draft of this item also claimed `PART_AUTHORING.md` contains no
tables, and that the "everything a part must touch" table `AGENTS.md` cites does
not exist. **Both were wrong.** The table is at `docs/PART_AUTHORING.md:45–53`;
it is blockquoted, which is why a `^\|` grep missed it. `AGENTS.md`'s citation is
correct and needs no change. See Appendix C.

**HP-12 — Remove the tracked scratch files**
*Files:* `scratch_head.txt`, `scratch_tail.txt`.
*Done when:* both are gone from the repository.
*Verify:* `git ls-files | grep scratch` returns nothing.
*Size:* S. Both are stray fragments of `tools/try_scene.py` committed to the
repository root.

---

## Phase 2 — The tag bus is the contract, and it has drifted

`AGENTS.md` calls the tag bus "the seam and the most important contract in the
project", and says two implementations speak it and the sidecar cannot tell them
apart. That is still *mostly* true, and the shared-fixture parity test
(`tests/test_tag_parity.py` against `engine/fixtures/tag_cases.json`) is a good
piece of work. But it covers the `Tag` model, not server behaviour, and the
servers have diverged underneath it.

The force feature is the sharper problem. `TAG_INSPECTOR_PLAN.md` argues,
correctly, that a forgotten force "explains more *why is my program not working*
than anything else here" — and the sidecar's own cache disagrees with the engine
about exactly those values.

**HP-13 — A forced output must read as forced from the sidecar**
*Files:* `engine/src/TagBus/TagBusServer.cs`,
`sidecar/factoryforge_sidecar/tagbus.py`, `.../tags.py`.
*Done when:* forcing an output is visible to a connected driver, and the
`forced` field survives description parsing instead of being discarded.
*Verify:* force a motor off while the PLC commands it on; the sidecar reports
off, and still reports off after the next local write.
*Size:* M. *Codex #9*: the server updates inputs only (`TagBusServer.cs:174`);
the client optimistically updates outputs and drops `forced` when parsing
(`tagbus.py:89`, `tags.py:94`). This defeats the diagnostic the feature exists
for.

**Do not fix this by broadcasting output changes through the ordinary update
path.** That invokes every driver's `push()` hook (`tagbus.py:229`–`:230`), and
`opcua_client.py:296`–`:298` writes every mapped update without checking `kind`
— so a naïve force-echo would write simulator-forced outputs back into
PLC-owned nodes, which is a worse bug than the one being fixed. Carry observed
output state on a separate channel from simulator-input delivery.
*Coordinate with:* HP-31, which reshapes that same path.

**HP-14 — Undo of a paste or duplicate must republish the tag set**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* both undo paths call `NotifyTagsChanged()`, as every other edit
does.
*Verify:* connect a driver, paste a section, undo, and confirm the driver's tag
list no longer contains the removed tags.
*Size:* S. *Codex #4*: `ForgetPart` is called without `NotifyTagsChanged` at
`SceneEditor.cs:2566` and `:2673`. `AGENTS.md` states the rule this breaks —
"changing the scene has to be announced."

**HP-15 — Renaming a part must not let a later part inherit its tags**
*Files:* `engine/src/Editor/PartTagManager.cs`,
`engine/src/Editor/SceneEditor.cs`.
*Done when:* auto-generated ids cannot collide with a renamed part's id, and a
loaded file containing duplicate ids is rejected or disambiguated rather than
adopted.
*Verify:* rename `conveyorbelt_1` to `conveyorbelt_2`, place a new conveyor, and
confirm the two do not share tags.
*Size:* M. *Codex #7*: renaming does not advance the counter
(`PartTagManager.cs:143`), so the next placement mints a colliding id and
registration adopts the existing tags. Two machines then answer one PLC output.

**HP-16 — A copied part must not keep the original's tag references**
*Files:* `engine/src/Editor/SceneEditor.cs`, `engine/src/Editor/PartProperties.cs`.
*Done when:* copy resets `Remover.CountTag` and anything else naming a tag
outside the part, or refuses to carry it across scenes.
*Verify:* copy a sorting-scene remover into an empty scene; it writes its own
`.count`, not `counter.tall`.
*Size:* M. *Codex #8*: copy resets instance ids (`SceneEditor.cs:2050`) but
preserves `CountTag`. In the source scene, duplicated removers then overwrite
one counter.

**HP-17 — Renaming a part must not release its forces**
*Files:* `engine/src/Editor/PartTagManager.cs`, `engine/src/TagBus/TagTable.cs`.
*Done when:* `TryRenamePart` carries the force state across, as it already
carries types, kinds and values.
*Verify:* force a motor off, rename its part, and confirm it is still forced
off.
*Size:* S. *Codex #14*: rename removes and rebuilds each tag, which clears the
force. Renaming a forced-off motor whose underlying command is true therefore
*starts the motor*.

**HP-18 — Close the three known parity divergences**
*Files:* `harness/engine_stub.py`, `engine/src/TagBus/TagBusServer.cs`,
`engine/src/TagBus/Tag.cs`, `engine/src/TagBus/TagTable.cs`,
`sidecar/factoryforge_sidecar/tags.py`.
*Done when:* malformed values inside a batch have one defined behaviour on both
sides; integer range is the same on both sides or the limit is in the protocol
spec; and the float epsilon `docs/tag-bus.md` advertises is actually applied
before publishing.
*Verify:* HP-19.
*Size:* M. *Codex #11, #12, #13*, and the first of the three is worse than the
first draft said. `harness/engine_stub.py:137` calls
`self.scene.tags.set(tag_id, value)` and its connection handler at `:88` catches
only `websockets.ConnectionClosed` — so a coercion error does not merely abandon
the batch, it escapes the handler and drops the connection. C# catches bad
values individually at `TagBusServer.cs:258`–`:264` and carries on. A batch
beginning with bit value `2` therefore disconnects one engine and is shrugged
off by the other. Separately, `2147483648` is fine in Python and rejected in C#;
and both tables store the new value even when `Differs` returns false, so a
signal moving by `1e-9` per scan publishes every scan.

**HP-19 — Parity must cover server behaviour, not just the model**
*Files:* `tests/test_tag_parity.py`, `engine/fixtures/`,
`engine/src/Sim/TagParitySelfTest.cs`, `tools/check_protocol.py`.
*Done when:* the shared fixture drives both *servers* over the wire — malformed
batches, integer boundaries, epsilon suppression — not only both `Tag` models.
*Verify:* reintroduce each HP-18 divergence and watch the check fail.
*Size:* L **for the three HP-18 divergences specifically; XL if "server parity"
is read broadly** — full parity would add lifecycle, epochs, force state,
malformed frames and reconnect behaviour. Fix the boundary before committing to
the estimate. `tools/check_protocol.py:31`–`:76` gives raw-wire scaffolding to
build on, but there is no shared server-fixture runner today.
*Do with:* HP-18 — demonstrate each mismatch first, then verify the fix. Extend
the fixtures to cover HP-13 and HP-23 if those are meant to be protected here.
*Size note:* *Codex #26*. This is the item that keeps the contract honest after
this plan closes.

**HP-20 — Redoing a duplicate must not mint a new identity**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* `DuplicateCommand` **and `DuplicateGroupCommand`** capture the
generated ids on first execute and reuse them on redo, the way `PartCommand`
already does.
*Verify:* duplicate a part, note its id, undo, redo; the id is unchanged. Repeat
with a multi-part selection and with Ctrl+V.
*Size:* S. *Codex #10*. The group path was missed in the first draft of this
item: `SceneEditor.cs:2078` supplies `Id = ""`, and `:2559` respawns that same
unchanged data on every execute, so paste and group-duplicate mint fresh ids on
each redo. `PartCommand`'s own comment explains why this matters:
"without it a redo minted a fresh id and silently broke any driver wiring
pointing at the old one." The duplicate path did not get the same treatment.

---

## Phase 3 — Drivers, and the one server that is not on loopback

The tag bus server and the OPC UA server both bind `127.0.0.1` deliberately,
with the reasoning written down. The Modbus *driver* does not: it overrides its
own server class's loopback default with `0.0.0.0`.

**HP-21 — `demo --duration` must do what AGENTS.md says it does**
*Files:* `sidecar/factoryforge_sidecar/__main__.py`.
*Done when:* `demo --duration N` exits cleanly after N seconds.
*Verify:* `python -m factoryforge_sidecar demo --driver mock --duration 5`
returns to the prompt.
*Size:* S. *Codex #40* — **confirmed**: the `demo` path awaits
`asyncio.Event().wait()` forever (`__main__.py:134`); `args.duration` is read
only on the `connect` path at `:203`, while the `demo` subparser accepts the
flag at `:304`. `AGENTS.md` gotcha 6 tells you to use it: *"Use
`demo --duration N`, which shuts down cleanly."* It does not shut down at all.

The first draft of this item argued the consequence was leaked OPC UA sessions
against the real S7. **That does not follow, and the argument was wrong.**
`__main__.py:135` catches `KeyboardInterrupt` and `asyncio.CancelledError`, and
the `finally` at `:137`–`:141` cancels the tasks and awaits `driver.stop()` and
`engine.stop()`. Ctrl-C is a clean shutdown path here; gotcha 6 is about
`os._exit()`, which this is not. So this is an ordinary papercut — a documented
flag that silently does nothing — and it is in this phase because it is an
`S` that makes a documented instruction true, not because it endangers the CPU.

**HP-22 — Modbus: loopback by default, and a server that survives contact**
*Files:* `sidecar/factoryforge_sidecar/drivers/modbus_tcp.py`,
`sidecar/factoryforge_sidecar/modbus/server.py`.
*Done when:* the driver defaults to `127.0.0.1` and binding wider is explicit;
frames are length-validated; reads have a deadline; connections are capped; and
`stop()` closes established clients.
*Verify:* a half-open client withholding bytes no longer pins a coroutine;
`stop()` ends existing sessions; a zero-length and a one-byte PDU are rejected
rather than indexing off the end.
*Size:* M. *Codex #30, #31* — bind **confirmed**: `modbus_tcp.py:60` is
`host: str = "0.0.0.0"` while `modbus/server.py:154` defaults to `127.0.0.1`.
The driver's default is what ships. Reads at `:183` and `:188` have no deadline;
`stop()` at `:171`–`:175` closes only the listener; an empty PDU reaches `pdu[0]`
at `:193` unguarded.

Exposure, corrected from the first draft: this does **not** reach everyone who
downloads a release. `DriverConnectionUI.cs:31` defaults `SelectedDriver` to
`"plcsim-advanced"`, and `:208` says *"No driver running. Apply & Connect starts
the Python sidecar."* Nothing listens until a user chooses Modbus and connects.
The exposure is real but scoped to people on the Modbus path — which, on a
classroom network, is still a writable simulator with no authentication that
anyone on the subnet can reach. One detail also overstated: a one-byte PDU for a
supported function raises `struct.error`, converted to `ModbusError` at
`:147`–`:148`, so it is the *empty* PDU that indexes off the end, not both.

**HP-23 — Non-finite analog values must be rejected at the edge**
*Files:* `engine/src/Editor/TagInspectorUI.cs`, `engine/src/TagBus/Tag.cs`,
`sidecar/factoryforge_sidecar/protocol.py`, `.../drivers/modbus_tcp.py`.
*Done when:* `NaN` and infinity cannot be forced through the UI, cannot be
coerced into a float tag, and cannot be decoded out of Modbus registers into a
JSON payload the C# parser will reject.
*Verify:* force `NaN` into a float tag; it is refused with a message.
*Size:* M. *Codex #29*.

**HP-24 — snap7 must not block the event loop, and must retry**
*Files:* `sidecar/factoryforge_sidecar/drivers/s7_snap7.py`.
*Done when:* connect, DB read and DB write run off the loop, and a failed
connection is reported as a failure and retried rather than logged and
swallowed.
*Verify:* start the driver against an unreachable address; the CLI says it is
not connected, bus traffic continues, and bringing the PLC up connects it.
*Size:* M. *Codex #33, #39*. Moving the calls off the loop is not by itself a
concurrency design: reads and writes share one `_client`
(`s7_snap7.py:154`–`:162`, `:171`), so access has to be serialized as well as
offloaded.
*Blocks:* HP-26, which needs this item's connection lifecycle to hang its
seeding off.

**HP-25 — A transient read error must not strand the OPC UA poller**
*Files:* `sidecar/factoryforge_sidecar/drivers/opcua_client.py`.
*Done when:* a read exception restarts polling rather than ending `_poll_loop`
while the connection loop sees a healthy session and does nothing.
*Verify:* inject a read failure; polling resumes.
*Size:* M. *Codex #34*.

**HP-26 — Native Siemens drivers must seed initial inputs**
*Files:* `sidecar/factoryforge_sidecar/drivers/s7_snap7.py`,
`.../plcsim_advanced.py`.
*Done when:* rebuild writes the current value of every simulator-owned input
once, so a stable-true input reaches the PLC.
*Verify:* start with the E-stop healthy and nothing moving; the PLC sees healthy
rather than tripped.
*Size:* M. *Codex #35*. Both native rebuilds only assign `self._table = table`
(`s7_snap7.py:124`, `plcsim_advanced.py:151`), and the engine publishes deltas
after the description (`TagBusServer.cs:177`), so an input that never changes is
never sent. The first draft said such an input "reads false at the PLC
indefinitely"; more precisely, the PLC keeps whatever that address already held,
which may be false, stale, or left over from a previous run — the point is that
nothing establishes it.

*Done when*, sharpened: seeding must fire whenever a connection and a current
table are both available, not only inside `rebuild`. Rebuild can precede
connection, and a reconnect does not necessarily bring a new description —
`tagbus.py:61` schedules description replay, while native `push()` simply
returns when disconnected (`s7_snap7.py:127`, `plcsim_advanced.py:154`).
*Depends on:* HP-24 for the connection lifecycle this hangs off.

**HP-27 — OPC UA subscriptions must not accumulate per rebuild**
*Files:* `sidecar/factoryforge_sidecar/drivers/opcua_client.py`,
`.../opcua_server.py`.
*Done when:* each rebuild deletes the subscription it replaces.
*Verify:* edit a scene ten times against a connected server and confirm one
subscription, not ten.
*Size:* S. *Codex #36*. Against the real S7 this spends a resource the CPU has
very little of — see gotcha 7.

**HP-28 — Coerce driver options at the boundary**
*Files:* `sidecar/factoryforge_sidecar/__main__.py`, `.../drivers/*.py`.
*Done when:* numeric and boolean `-o` options are converted once, centrally, and
an unparseable value is refused with a message.
*Verify:* `-o poll_interval 0.05` polls at 0.05 s; `-o auto_map false` disables
it.
*Size:* S. *Codex #38*. `AGENTS.md` gotcha 19b already records this exact class
of bug for snap7's `db`; it was fixed in one driver and left as a pattern.
`"0.05"` reaches `asyncio.sleep` and raises before the poller's handler, leaving
an unobserved failed task while the connection loop reports health.

**HP-29 — The OPC UA server must be able to enable the security it mentions**
*Files:* `sidecar/factoryforge_sidecar/drivers/opcua_server.py`.
*Done when:* certificate and authentication options passed through driver
options are applied, or the comment stops implying they can be.
*Verify:* start server mode with a certificate and confirm the endpoint offers
it.
*Size:* M. *Codex #32*. The default loopback endpoint limits the exposure; the
code comment says "deployments that need certificates can configure them", and
`start()` selects `NoSecurity` unconditionally.

**HP-30 — Tools must leave the engine as they found it**
*Files:* `tools/check_protocol.py`, `tools/test_plan.py`.
*Done when:* the protocol checker restores whatever force state it found —
skipping an already-forced tag, or putting the original force back in a
`finally` — rather than blanket-releasing, which would itself destroy a
deliberate override. `tools/check_protocol.py:53`–`:60` picks any bit input and
overwrites its visible state, so "release what I set" is not the same as "leave
it as I found it". And `EngineProcess.__enter__` cleans up the process and log
handle when startup fails.
*Verify:* run the checker against a live engine and confirm no force remains;
make the port never appear and confirm no orphan Godot process.
*Size:* S. *Codex #41, #42*. A checker that pins a sensor and reports success
silently changes the exercise a student is doing.

**HP-31 — Decouple driver I/O from the bus receive loop**
*Files:* `sidecar/factoryforge_sidecar/tagbus.py`,
`sidecar/factoryforge_sidecar/drivers/__init__.py`.
*Done when:* a slow PLC write does not delay the next sensor update or scene
description.
*Verify:* a driver with an artificial 500 ms write still lets updates flow.
*Size:* L. *Codex #20*. This is the one architectural item in this phase, and it
gets worse as scenes gain I/O.

**HP-32 — The Siemens tests must test something**
*Files:* `tests/test_siemens.py`.
*Done when:* the tests start, rebuild, poll, push and stop a driver against a
fake, covering DB offsets, direction handling and connection failure.
*Verify:* break an offset and watch a test fail.
*Size:* M. *Codex #23*: `test_siemens.py:17` asserts construction and
attributes. `AGENTS.md` records that all three Siemens drivers once shipped
calling `TagTable.outputs()` and `tag.kind.value`, neither of which exists.
Nothing in the suite would catch that today either.

**HP-33 — Confirm or dismiss the epoch/poll race**
*Files:* `sidecar/factoryforge_sidecar/drivers/opcua_client.py`.
*Done when:* either a reproduction exists and the fix lands, or the reasoning
for why it cannot happen is written into the file.
*Verify:* delay a rebuild and complete an old poll inside the window.
*Size:* M. *Codex #37*, marked by Codex as a hypothesis. Treat it as one:
reproduce before rewriting.
*Settle with:* HP-25, HP-27 and HP-31 — all four touch the same two orderings.
`_bind()` changes its maps before cancelling the old poller
(`opcua_client.py:175`–`:207`), and the client adopts a new epoch before
awaiting the rebuild hooks (`tagbus.py:216`–`:223`). Decide who owns cancellation
and who owns the epoch *before* moving any of this work onto background queues,
or HP-31 will bake the race in.

---

## Added by implementing it — HP-53 … HP-56

Four findings that only appeared once the plan was being worked on in parallel.
Three are the same defect wearing different clothes, and the plan had already
named the family without noticing it had members here: **a fixed port is a
shared resource, and a test that needs one cannot run twice at once.**

**HP-53 — No fixed ports in anything a second run might also start** ✅ **done**
*Files:* `tools/test_plan.py`, `tools/try_scene.py`, `tools/check_protocol.py`,
`tools/check_force_types.py`, `tools/check_force_while_paused.py`,
`tools/drive_engine.py`, `tools/live_driver.py`, `tests/test_opcua.py`.
*Done when:* two runs of the Python suite, and two runs of the test plan, can
execute simultaneously on one machine and both pass.
*Verify:* run each twice at once and read both results.
*Size:* M.

The engine's bus port was 7411 in six places in `test_plan.py`, three more in
the tools it invokes, and one in `try_scene.py`, which starts its own engine.
`tests/test_opcua.py` pinned 48400 and 48410. All of it now comes from
`FF_BUS_PORT` / `FF_BUS_URL`, defaulting to a free port the OS picks, with the
old values as the fallback so nothing outside had to change.

**The fix was proved by running two plans at once, and that experiment is the
item's real content.** The first attempt still failed — but only at F2 and F3,
on `[winerror 10048] only one usage of each socket address`, while every other
check passed in both runs. The bus port was fixed; the *drivers'* listening
ports, Modbus 502 and OPC UA 4841, were not. Reasoning about the change would
have stopped at the bus and declared it done. Both now take a free port too.

**HP-54 — A wait must be able to observe the thing it waits for** 
*Files:* wherever an `until`/poll loop waits on another process's output.
*Done when:* every such wait has a timeout and a liveness check on its target,
and says which of the two ended it.
*Verify:* kill the watched process and confirm the waiter exits and says so.
*Size:* S.

A poll loop waiting for a verification run to print a sentinel string ran for
**52 minutes** during this work. The run it watched had been killed at step 4
of 8, so the sentinel was never written and the condition could never become
true. Nothing was broken and nothing was learned; the loop simply could not
tell "not finished yet" from "never going to finish".

That is gotcha 16 with a different subject — a check that cannot observe its own
failure mode — and it is the same shape as HP-30's leaked `EngineProcess`, where
a startup that never bound the port left a process nobody was waiting on.

**HP-55 — CI must install `snap7`, or the Siemens suite measures its own fake** ✅ **done**
*Files:* `.github/workflows/test-plan.yml`.
*Done when:* CI installs `sidecar[dev,siemens]` and
`test_the_fake_agrees_with_real_snap7` actually runs there.
*Verify:* the B1 line reports one more passing test than it did.
*Size:* S.

HP-32's new Siemens tests assert byte offsets against a `FakeUtil` stand-in, and
`tests/test_siemens.py:300` exists to check that fake against the real library —
otherwise every offset assertion is measuring the fake against itself. It opens
with `pytest.importorskip("snap7.util")`, and CI installed `sidecar[dev]`, so it
has never run there. Found by chasing a one-test discrepancy: CI reported 137
passing where Windows reported 138. `snap7` is a pure pip install and needs no
Siemens software.

**HP-56 — B1 should report skips, not just passes**
*Files:* `tools/test_plan.py`.
*Done when:* the B1 line names skipped tests as well as passed and failed ones.
*Verify:* skip one deliberately and read the line.
*Size:* S.

`section_b` greps `(\d+) passed` and `(\d+) failed`. A skip is neither, so a
test that silently stops running anywhere is invisible in the plan's own output
— which is exactly how HP-55 hid. The count was accurate and incomplete, and
"accurate and incomplete" is how a suite quietly shrinks.

---

## Added by the second review — HP-47 … HP-52

Six findings neither the first Codex pass nor this review caught. They are
collected here rather than renumbered into the phases above, because
renumbering forty-six items to insert six is how citation errors get made. Each
names its thematic home; two of them, HP-51 and HP-52, are the same shape as
each other and worth reading together — in both, a lesson this project learned
the hard way and wrote into `AGENTS.md` was never applied to the code that
taught it.

**HP-47 — Saving must be atomic, and must not destroy the last good file**
*Home:* Phase 0.
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* a save writes to a temporary file, verifies the write completed,
and only then replaces the destination. An interrupted save leaves the previous
scene file intact.
*Verify:* kill the process mid-save; the existing file is unchanged and
loadable.
*Size:* M. Found by the second Codex pass. `SceneEditor.cs:2351`–`:2352` opens
the destination directly for writing and stores JSON into it, so a crash, a full
disk or a removed drive midway through leaves a truncated file where a working
scene used to be. HP-01 makes failure *visible*; this makes it *survivable*, and
the second is the one that matters when the file being overwritten is the only
copy.

**HP-48 — Modbus addresses must not move when a scene is edited**
*Home:* Phase 3.
*Files:* `sidecar/factoryforge_sidecar/drivers/modbus_tcp.py`.
*Done when:* adding or renaming a part does not shift the addresses of tags that
already existed, or the remap is explicit and announced.
*Verify:* note a coil address, add a part whose id sorts alphabetically before
it, and confirm the address is unchanged.
*Size:* M. Found by the second Codex pass. `modbus_tcp.py:89`–`:101` rebuilds
every address from zero, in `sorted(table, key=lambda t: t.id)` order, on each
rebuild. Add a conveyor called `belt_a` to a scene that already has `pusher_1`
and every address after it slides by one — while the PLC's configuration, which
is a hand-written list of addresses, does not. The symptom is a program that was
working and now drives the wrong device, with nothing on screen to explain it.
This is the Modbus equivalent of HP-15, and it is worse, because Modbus has no
tag names on the wire to notice the mismatch with.

**HP-49 — Modbus integers must carry the range the tag promises**
*Home:* Phase 3.
*Files:* `sidecar/factoryforge_sidecar/drivers/modbus_tcp.py`, `docs/tag-bus.md`.
*Done when:* either an Int tag maps to two registers over Modbus, or the
narrowing to 16 bits is documented and range-checked rather than silent.
*Verify:* drive a counter past 32,767 and read it back.
*Size:* M. Found by the second Codex pass. `_to_registers` at `modbus_tcp.py:48`
is `[int(value) & 0xFFFF]` and `_from_registers` at `:54`–`:55` reinterprets that
as signed 16-bit, so a C# 32-bit Int tag silently wraps. A carton counter on a
long run is exactly the tag that finds this. Note that HP-18's Python/C# range
work does **not** fix it: this is a transport representation, not a model
disagreement.

**HP-50 — A dropped simulator-input write must be retried**
*Home:* Phase 3.
*Files:* `sidecar/factoryforge_sidecar/drivers/opcua_client.py`.
*Done when:* a failed input write is retained and retried, or inputs are
periodically reconciled, rather than logged and dropped.
*Verify:* fail one write to a sensor that then stops changing; the PLC still
converges on the right value.
*Size:* M. Found by the second Codex pass. `opcua_client.py:313`–`:315` logs a
failed write and discards it. Because the engine publishes deltas
(`TagBusServer.cs:177`–`:179`), a sensor that fails its write and then holds
steady is never sent again — the PLC keeps the wrong value indefinitely, on a
connection that reports healthy. This is the same failure shape as HP-26,
arriving by a different route.

**HP-51 — Honour the connect timeout AGENTS.md already prescribes**
*Home:* Phase 3.
*Files:* `sidecar/factoryforge_sidecar/drivers/opcua_client.py`.
*Done when:* the client is constructed with an explicit, configurable timeout
defaulting to 10 s, and a test covers that it is propagated.
*Verify:* connect to a slow endpoint and confirm it is not abandoned at 4 s.
*Size:* S. Found by the second Codex pass, and it is a straight contradiction of
the project's own record. `AGENTS.md` gotcha 8 says *"asyncua's default 4 s
connect timeout is too short for a real S7. Use `timeout=10`."*
`opcua_client.py:142` is `Client(url=self.url)`, with no timeout argument. The
lesson was learned, written down, and never applied to the code.

**HP-52 — snap7 must stop repeating the misdiagnosis gotcha 19c records**
*Home:* Phase 3.
*Files:* `sidecar/factoryforge_sidecar/drivers/s7_snap7.py`.
*Done when:* an "Invalid address" error reports overrunning the DB as the likely
cause, alongside optimized block access, rather than asserting the latter.
*Verify:* read past the end of a 10-byte DB and read the message.
*Size:* S. Found by the second Codex pass, and the same shape as HP-51.
`s7_snap7.py:204`–`:212` matches `"Invalid address"` and tells the user their DB
is an optimized-block-access DB, with TIA Portal instructions. `AGENTS.md`
gotcha 19c says: *"snap7's 'Invalid address (0x05)' usually means the read
overran the DB, not that the block is optimized. FF_IO is 10 bytes; asking for
12 fails exactly that way. I misdiagnosed this as optimized block access and was
wrong."* The correction was written into the handoff document and the wrong
diagnosis is still hard-coded into the error path, where it will send the next
person down the same afternoon the gotcha exists to save them from.

---

## Phase 4 — Make a part cost one file

This is the phase the PRD's twelve-month success criteria depend on: "25+ parts,
of which a meaningful share are community-contributed" and "contributors have
added at least one driver we did not write." Neither is reachable while a part
costs five files.

**HP-34 — A part owns its own tags, properties and tick**
*Files:* new `engine/src/Parts/PartBase.cs` or `IPart`;
`engine/src/Editor/PartTagManager.cs`, `PartProperties.cs`,
`PartPropertyInspectorUI.cs`, `SceneEditor.cs`, and every part class.
*Done when:* tag registration, property capture and per-tick dispatch live on
the part, and the editor asks the part rather than switching on its type name.
Adding a part is one new file plus one `PartCatalog` entry.
*Verify:* add a trivial new part touching exactly two files, and have
`--self-test=scene` pick it up with no other edit.
*Size:* XL, and budget multiple weeks rather than treating XL as a ceiling.
*Codex #21, #22*. Today `PartTagManager.cs:153` is a `switch (partType)`, the
node factory is a `return partType switch` at `SceneEditor.cs:3472`, and
`PartProperties.cs` and `PartPropertyInspectorUI.cs` carry 48 and 23 per-type
branches respectively (counting `is <Type> <name>` patterns and `case "<Type>"`
labels; `SceneEditor.cs` has 56 by the same count). Historically,
`SceneEditor.cs` grew a net **183** lines across the five-part commit `1162592`
(184 added, 1 removed) and a net **279** across the nine-part commit `ce3f570`
(281 added, 2 removed).

Scope is wider than three dispatch tables, and the first draft of this item
understated it. `docs/PART_AUTHORING.md:45–53` names eight integration points,
including `PlacedPart.TagSuffixesByType` (`SceneEditor.cs:93`) and
`WholeBodyOperableTag` (`:1137`, used for operator hit testing at `:1388`) —
both centralized per-part metadata tables, not switches. Add to that
construction, inheritance between part types, settings applied before `_Ready`,
and `PartSettingsSelfTest.cs:380`–`:384`, which rejects new settings rows
without explicit checks. Write the migration criteria per integration point
before starting.

**HP-35 — Fix the drift this design already produced**
*Files:* `engine/src/Editor/PartTagManager.cs`, `engine/src/Editor/SceneEditor.cs`.
*Done when:* the weighing conveyor's `.fault` tag stops the belt, as every other
conveyor's does.
*Verify:* force `.fault` on a weighing conveyor and watch it stop.
*Size:* S. *Codex #21*, and the fix is one line. `PartTagManager.cs:239`
registers `<id>.fault` for `WeighingConveyor` as a `Bit`/`Input`, exactly as the
adjacent `VariableConveyor` case does at `:250`. But `SceneEditor.cs:3269`–
`:3272`, inside `case "WeighingConveyor"`, calls only `SetRunning` and publishes
weight — while the `VariableConveyor` case at `:3130` calls `SetFaulted`, as do
nine other parts. `WeighingConveyor` derives from `ConveyorBelt`, so it
*inherits* `SetFaulted` (`ConveyorBelt.cs:207`); the method is there and is
simply never called for this subclass.
*Do not defer this behind HP-34.* It is an advertised contact that does nothing,
which is a contract defect on its own terms, and it is one line.

This is what the anti-goal looks like in practice: a tag that exists, appears in
the inspector, can be forced, and does nothing. Nobody decided that. It is the
failure mode of a design where registering a tag and acting on it are edits to
two different files.

**HP-36 — A test that fails when a part type is special-cased again**
*Files:* new check in `tools/test_plan.py` or a self-test.
*Done when:* a concrete part type name appearing anywhere outside its own part
class fails a check — not only in a `switch` or type test, but in the
centralized per-part metadata tables too. A ban on switches alone would miss
`TagSuffixesByType` (`SceneEditor.cs:93`) and `WholeBodyOperableTag` (`:1137`),
which are dictionaries keyed by type name and are exactly where the knowledge
would pool next.
*Verify:* add one back, in each of the three shapes, and watch it fail.
*Size:* M. *Depends on:* HP-34. Without this, HP-34 decays.

**HP-37 — One definition of part identity across commands**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* placement, deletion, move and duplicate resolve a part the same
way, and composite commands inherit it.
*Verify:* two overlapping conveyors with different ids and settings; deleting
the selected one removes that one.
*Size:* M. *Codex #22*: today placement and deletion resolve by type and
position, move retains node references, and duplicate retains snapshots.

---

## Phase 5 — The editor, as operated rather than as tested

These are the ones a self-test cannot see because it drives the API rather than
the keyboard and the camera — the exact gap `AGENTS.md` gotcha 24 was written
about.

**HP-38 — One key, one action**
*Files:* `engine/src/Editor/SceneEditor.cs`, `engine/src/Main.cs`.
*Done when:* Ctrl+C copies without toggling the camera, and Ctrl+R does not both
rotate the selection and reset the simulation.
*Verify:* press each with a part selected.
*Size:* S. *Codex #15*. Both are fall-through, and the fix pattern is already in
the file. `SceneEditor.cs:465` handles Ctrl+C and calls `CopySelection()` but
never marks the event handled, so it reaches `Main.cs:654` — `else if
(keyEvent.Keycode == Key.C)`, with no modifier exclusion — and toggles the
camera too. Ctrl+R is the mirror image: `SceneEditor.cs:481` rotates on `Key.R`
without excluding Ctrl, and `Main.cs:624` resets the simulation on Ctrl+R. Two
lines below the rotate branch, `Key.N` at `SceneEditor.cs:486` is guarded with
`&& !keyEvent.CtrlPressed`, so the convention exists and was applied to one key
out of three.

**HP-39 — Arrow-key nudge must follow the screen**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* Right moves the selection right in every standard view.
*Verify:* press `3` for the front view, then Right.
*Size:* S. *Codex #16*: in the front view the camera's forward is −Z, so
`Atan2(forward.X, forward.Z)` gives ≈π and Right maps to −X. The headless
fallback has no camera, so `--self-test=buildflow` cannot see it — a fresh
instance of the bug gotcha 24 describes.

**HP-40 — A cleared filter must restore the rows it hid**
*Files:* `engine/src/Editor/TagInspectorUI.cs`,
`engine/src/Sim/TagForceSelfTest.cs`.
*Done when:* clearing a search reopens group bodies, not just headers; and the
self-test checks visibility *through parents* rather than a row's own `Visible`
flag.
*Verify:* search one machine, clear the search, and read the other group.
*Size:* S. *Codex #17, #25*. The test passes through this bug today, for the
reason named in #25.

**HP-41 — A placement preview must not touch the simulation**
*Files:* `engine/src/Editor/SceneEditor.cs`.
*Done when:* the preview part has collisions and monitoring disabled until it is
committed.
*Verify:* with the simulation running in Edit mode, sweep a remover preview over
a carton; the carton survives.
*Size:* **M**, revised up from S. *Codex #19*: `ArmPreview`
(`SceneEditor.cs:279`) builds an ordinary part and `AddChild`s it at `:289`, so
a remover preview deletes cartons before placement — `Remover.cs:36` connects
`BodyEntered` and `:84` calls `box.QueueFree()` — and cancelling cannot bring
them back. The size moves because parts build live collision and monitoring
nodes during their own `_Ready` (`Remover.cs:27`–`:36` is the clearest case), so
a reusable answer has to reach into the part, not tint the root node; and the
verification has to cover both physical collision and active `Area3D`
monitoring.

---

## Phase 6 — Let someone else in

**HP-42 — A contribution path that exists**
*Files:* new `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
`.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md`.
*Done when:* all exist, and `CONTRIBUTING.md` points at `PART_AUTHORING.md` and
`DRIVER_AUTHORING.md` as the two ways in.
*Verify:* the repo's community-profile page is complete.
*Size:* S. *Depends on:* HP-34 for the part path to be honest.

**HP-43 — Separate the live documents from the closed ones**
*Files:* `docs/` → `docs/history/`.
*Done when:* the thirteen closed plan documents move under `docs/history/`,
leaving `PRD`, `ROADMAP`, `TEST_PLAN`, the two authoring guides,
`GETTING_STARTED`, `PACKAGING`, `tag-bus.md` and this file as the live set.
Links are updated.
*Verify:* every link in `README.md` and `AGENTS.md` resolves.
*Size:* S. Found here: tracked `docs/*.md` is **8,053** lines, or 8,739 counting
`README.md` and `AGENTS.md` alongside it, against roughly 34,570 tracked lines
of `.cs` and `.py`. About 5,400 of the documentation is completed plans.
(`wc -l` over `git ls-files`; the first draft quoted the 8,739 figure while
describing it as `docs/` alone.) `OPERATOR_PLAN.md` (342 lines) and
`SESSION-NOTES.md` (593 lines) are linked from nothing at all. They are worth
keeping — this project's plan documents are unusually good history — but a new
contributor cannot tell which of them is live.

**HP-44 — Widen CI to the sections it skips**
*Files:* `.github/workflows/test-plan.yml`.
*Done when:* `F` and `G` run on Linux in CI.
*Verify:* a green run including both.
*Size:* M. Found here: both pass 12 of 12 locally on Windows. The workflow
comment says they are excluded because process lifecycle and port timing have
"only ever been run and tuned on Windows so far" and FF-08 is open — so this is
the FF-08 work, not new work. It also gives HP-21 and HP-30 somewhere to be
caught.

**HP-45 — An unmodified OpenPLC program drives the scene over Modbus**
*Files:* `examples/`, `docs/`.
*Done when:* the sorting scene runs from OpenPLC over the existing Modbus
driver, and the program and wiring are in `examples/`.
*Verify:* run it and count the split.
*Size:* L. `PRD.md:92` lists this as a **launch** criterion — it is the one that
proves the project is not Siemens-only — and `AGENTS.md` records it as
"deliberately skipped at the user's request".

**This item is a scope decision, not an obligation.** It was deferred
deliberately and stays deferred until the user says otherwise; it appears here
so that the PRD and the roadmap stop disagreeing about it. Either reopen it or
strike it from the PRD's launch criteria — what it should not do is sit
unmet and unmentioned.

**HP-46 — The same TIA program over both drivers**
*Files:* `docs/`, `examples/tia/`.
*Done when:* `Sorting.scl` drives PLCSIM Advanced through the OPC UA driver and
the Modbus driver with the same behaviour, and the result is recorded.
*Verify:* the split counts from both paths.
*Size:* M. The other unmet `PRD.md:92` launch criterion. Also the natural place
to finally document the 100-variable unlicensed trial limit and the "PLCSIM
Advanced is required, plain PLCSIM will not work" finding, both still open in
`ROADMAP.md:58`.

**There is a transport prerequisite, and "the same program" needs defining.**
The sidecar's Modbus support is a *server* (`modbus_tcp.py:3`–`:4`), so the PLC
has to be the client — which is communication code the S7 does not have today.
`examples/tia/Sorting.scl` is the sorting block, not a transport. State plainly
what "same program" means: identical sorting logic, plus whatever
communication blocks each path requires. Otherwise this criterion cannot be
honestly marked done.
*Depends on:* HP-48 and HP-49, both of which change what a Modbus client sees.

---

## Sequencing

The phases above are **thematic**, not a priority order. Priority is set by one
list instead.

### The release gate

These must land before HP-09, whatever phase they sit in. The rule is: anything
that silently destroys a user's work, or that silently lies to the PLC, ships
only once it is fixed — because a release is the moment the number of people
hitting it stops being one.

| Item | Why it gates |
|---|---|
| HP-01, HP-47 | a save that fails invisibly, and one that takes the last good file with it |
| HP-02 | opening a bad file destroys the good scene |
| HP-03, HP-04, HP-05, HP-37 | undo returns something other than what was there |
| HP-06 | configured settings do not survive a save |
| HP-07 | a newer scene file half-loads instead of being refused |
| HP-08 (regressions) | none of the above stay fixed otherwise |
| HP-15, HP-17 | a rename silently reassigns tags, or releases a force |
| HP-13 | the sidecar reports an output state that is not the one in effect |
| HP-16, HP-20 | copied and re-done parts carry or mint the wrong identity |
| HP-23 | a non-finite analog value corrupts a plant calculation or blocks a batch |
| HP-26, HP-50 | the PLC holds an input value nothing will ever correct |
| HP-22 | the Modbus default listens on every interface |
| HP-35 | an advertised fault contact does nothing |
| HP-41 | a preview deletes cartons that cannot be brought back |
| HP-11, HP-12 | the docs a new user reads first are wrong about the basics |

That list is larger than Phase 0, and deliberately so: the first draft of this
document put HP-13 through HP-17, HP-20, HP-23, HP-26, HP-35, HP-37 and HP-41
into later phases on thematic grounds, and they meet Phase 0's own stated
criterion — silent destruction or silent misreporting. The phase headings were
organising by *subsystem* while claiming to organise by *severity*.

### After the gate

* **Phase 2's remainder** if the priority is the project's stated crown jewel.
  HP-19 is what stops the contract drifting again after this plan closes.
* **Phase 4** if the priority is contributors. It is the only phase that changes
  who can work on this. It is also the largest and the likeliest to slip, and
  HP-35 should not wait for it.
* **Phase 3's remainder** is driver reliability, and it is the phase that gets
  exercised hardest the first time somebody who is not the author connects real
  hardware.

**On the previous draft's tiebreak.** It argued HP-22 should come first because
it is "the only item that gets worse the moment HP-09 succeeds." That is not
sound. Every shipped defect on this list gets worse with distribution — HP-07
makes precisely that argument about scene sharing — and HP-22's exposure is
narrower than claimed, since the app defaults to `plcsim-advanced` and starts
nothing until *Apply & Connect* (`DriverConnectionUI.cs:31`, `:208`). HP-22
belongs in the gate on its own merits, not as a tiebreak. The gate replaces the
tiebreak.

---

## Appendix A — work item index

Sizes below are the revised ones. **Gate** marks an item on the release gate.

| Item | Title | Phase | Size | Gate | Status |
|---|---|---|---|---|---|
| HP-01 | A failed save must not report success | 0 | S | ● | open |
| HP-02 | Validate a scene file before destroying the open one | 0 | M | ● | open |
| HP-03 | Undoing a delete must restore the part's settings | 0 | S | ● | open |
| HP-04 | `M`-move must undo as a move | 0 | M | ● | open |
| HP-05 | Command history must not hold freed nodes | 0 | M | ● | open |
| HP-06 | Settings a part exposes must survive a save | 0 | M | ● | open |
| HP-07 | Read the scene-file version already being written | 0 | S | ● | open |
| HP-08 | Make the tests able to see Phase 0 | 0 | M / L | ● (regressions) | open |
| HP-09 | Publish a real release | 1 | S |  | open |
| HP-10 | Make the project findable | 1 | S |  | open |
| HP-11 | Reconcile the documentation with the code | 1 | S | ● | open |
| HP-12 | Remove the tracked scratch files | 1 | S | ● | open |
| HP-13 | A forced output must read as forced from the sidecar | 2 | M | ● | open |
| HP-14 | Undo of a paste must republish the tag set | 2 | S |  | open |
| HP-15 | Renaming must not let a later part inherit its tags | 2 | M | ● | open |
| HP-16 | A copied part must not keep the original's tag refs | 2 | M | ● | open |
| HP-17 | Renaming a part must not release its forces | 2 | S | ● | open |
| HP-18 | Close the three known parity divergences | 2 | M |  | open |
| HP-19 | Parity must cover server behaviour | 2 | L / XL |  | open |
| HP-20 | Redoing a duplicate must not mint a new identity | 2 | S | ● | open |
| HP-21 | `demo --duration` must do what AGENTS.md says | 3 | S |  | open |
| HP-22 | Modbus: loopback by default, and a hardened server | 3 | M | ● | open |
| HP-23 | Non-finite analog values rejected at the edge | 3 | M | ● | open |
| HP-24 | snap7 must not block the loop, and must retry | 3 | M |  | open |
| HP-25 | A transient read error must not strand the poller | 3 | M |  | open |
| HP-26 | Native Siemens drivers must seed initial inputs | 3 | M | ● | open |
| HP-27 | OPC UA subscriptions must not accumulate | 3 | S |  | open |
| HP-28 | Coerce driver options at the boundary | 3 | S |  | open |
| HP-29 | The OPC UA server must be able to enable security | 3 | M |  | open |
| HP-30 | Tools must leave the engine as they found it | 3 | S |  | open |
| HP-31 | Decouple driver I/O from the bus receive loop | 3 | L |  | open |
| HP-32 | The Siemens tests must test something | 3 | M |  | open |
| HP-33 | Confirm or dismiss the epoch/poll race | 3 | M |  | open |
| HP-34 | A part owns its tags, properties and tick | 4 | XL |  | open |
| HP-35 | Fix the drift this design already produced | 4 | S | ● | open |
| HP-36 | A test that fails when a part is special-cased | 4 | M |  | open |
| HP-37 | One definition of part identity across commands | 4 | M | ● | open |
| HP-38 | One key, one action | 5 | S |  | open |
| HP-39 | Arrow-key nudge must follow the screen | 5 | S |  | open |
| HP-40 | A cleared filter must restore the rows it hid | 5 | S |  | open |
| HP-41 | A placement preview must not touch the simulation | 5 | M | ● | open |
| HP-42 | A contribution path that exists | 6 | S |  | open |
| HP-43 | Separate the live documents from the closed ones | 6 | S |  | open |
| HP-44 | Widen CI to the sections it skips | 6 | M |  | open |
| HP-45 | OpenPLC drives the scene over Modbus | 6 | L |  | open |
| HP-46 | The same TIA program over both drivers | 6 | M |  | open |
| HP-47 | Saving must be atomic | 0 | M | ● | open |
| HP-48 | Modbus addresses must not move when a scene is edited | 3 | M |  | open |
| HP-49 | Modbus integers must carry the range the tag promises | 3 | M |  | open |
| HP-50 | A dropped simulator-input write must be retried | 3 | M | ● | open |
| HP-51 | Honour the connect timeout AGENTS.md prescribes | 3 | S |  | open |
| HP-52 | snap7 must stop repeating gotcha 19c's misdiagnosis | 3 | S |  | open |
| HP-53 | No fixed ports in anything a second run might start | 3 | M |  | **done** |
| HP-54 | A wait must be able to observe what it waits for | 3 | S |  | open |
| HP-55 | CI must install snap7, or the Siemens suite tests its own fake | 6 | S |  | **done** |
| HP-56 | B1 should report skips, not just passes | 6 | S |  | open |

## Appendix B — where the findings came from

Three reviews on 2026-09-20 against `f597e27`.

**Codex pass 1**, read-only, forty-two findings. All forty-two are carried here;
the mapping is in the *Codex #n* notes on each item. Six were re-verified by
reading the code and are marked **confirmed** — #3 (HP-03), #5 (HP-02), #6
(HP-01), #27 (HP-08), #40 (HP-21), and the Modbus bind address in #31 (HP-22).

**This review**, which contributed the release and repository findings (HP-09,
HP-10), the documentation drift (HP-11, HP-43), the tracked scratch files
(HP-12), the unread scene version (HP-07), the CI coverage gap (HP-44), and the
scoring of the PRD launch criteria that shapes Phase 6.

**Codex pass 2**, over the first draft of this document. It corrected six claims
(Appendix C), found four completion criteria too narrow to resolve the finding
they cited (HP-01, HP-20, HP-26, HP-30), disputed the phase assignment of ten
items — which produced the release gate — and added six findings neither earlier
pass caught: HP-47 through HP-52.

### What is *not* wrong

Most of it. Distinguishing what was **executed** from what was **read** matters
here, because Codex pass 2 correctly declined to confirm the first group from
source alone:

*Executed in this session, on Windows:* the C# build is clean at zero warnings;
73 of 73 Python tests pass; test-plan sections F and G pass 12 of 12.

*Established by reading:* twenty-nine `*SelfTest.cs` files exist; `test_plan.py`
registers C1 through C28 and CI runs section C; both parity implementations load
the same `engine/fixtures/tag_cases.json`, which is a genuinely good piece of
design within its scope; `TagBusServer.cs:46` binds `127.0.0.1` and
`opcua_server.py:32` and `:86`–`:88` set a loopback endpoint with the reasoning
written down.

*Asserted from the GitHub API and not verifiable from a checkout:* zero
releases, zero tags, zero stars, no repository description, and three historical
`workflow_dispatch` runs of `release.yml`. Anyone re-checking this document
should re-query rather than trust it.

The weaknesses here are concentrated in three places — what happens when an
operation fails, what happens on undo, and what it costs to add a part.

---

## Appendix C — corrections to the first draft

Kept rather than silently fixed, because two of them were severity arguments
built on unchecked assumptions, which is the failure this document criticises in
`--self-test` coverage. Four were mine; two were inherited from Codex pass 1.

| # | Claim in the first draft | Correction |
|---|---|---|
| 1 | *"`PART_AUTHORING.md` contains no tables at all"*, and the table `AGENTS.md` cites does not exist (HP-11) | **False.** The table is at `docs/PART_AUTHORING.md:45`–`:53`, blockquoted, which is why a `^\|` grep missed it. `AGENTS.md`'s citation is correct. It names *eight* integration points, which strengthens HP-34. |
| 2 | Ignoring `demo --duration` leaks OPC UA sessions against the S7, breaking gotcha 6 (HP-21) | **Does not follow.** `__main__.py:135`–`:141` catches `KeyboardInterrupt` and awaits `driver.stop()` and `engine.stop()`. Ctrl-C is clean; gotcha 6 is about `os._exit()`. Real bug, ordinary severity. |
| 3 | HP-22 "ships a Modbus server on every interface to everyone who downloads it", and is the sequencing tiebreak | **Overstated.** `DriverConnectionUI.cs:31` defaults to `plcsim-advanced` and `:208` confirms nothing starts until *Apply & Connect*. The tiebreak argument was unsound and has been replaced by the release gate. |
| 4 | "Both delete paths — group at `:2120` and single at `:2907`" (HP-03) | `:2907` is the **placement** commit (`isPlacement: true` at `:2910`). There is one delete path; `ClearSceneCommand` at `:2729` is separate. |
| 5 | `docs/` is 8,739 lines (HP-43) | Tracked `docs/*.md` is **8,053**; 8,739 includes `README.md` and `AGENTS.md`. The figure was right, the scope label was not. |
| 6 | `SceneEditor.cs` grew 185 and 283 lines across the two part commits (HP-34) | Net growth is **183** and **279**; the larger numbers counted additions without subtracting deletions. |

Two further corrections were made to my own text before this pass, while
checking my citations: `GETTING_STARTED.md:27` → `:28`, and HP-38's mechanism,
which is event fall-through rather than a missing modifier check — both branches
do test `CtrlPressed`.
