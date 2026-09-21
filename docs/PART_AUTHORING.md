# 🛠️ FactoryForge Part Authoring Guide

This guide explains how to author custom 3D factory components for **FactoryForge**.

---

## 🏗️ Part Architecture Overview

Every factory component in FactoryForge is a Godot C# node located in `engine/src/Parts/`. Components read output tags written by the PLC (motors, solenoids, lamps) and update input tags read by the PLC (optical sensors, limit switches, encoders).

### Four rules that are not obvious

**1. A part's origin sits on the work plane.** `PartLayout.WorkPlaneY` (y = 0.5)
is the conveyor height, and it is where the scene editor drops every part. Your
part offsets its own geometry from there — legs reach *down* by
`PartLayout.FloorDrop`, anything that sits on a belt clears
`PartLayout.BeltSurface`. Never bake a mounting height into a scene position, or
the part will hover when someone places it from the palette.

**2. The instance id is a tag *prefix*, never a whole tag name.** The dispatch
appends the suffix, so a part registered as `"conveyor"` resolves
`conveyor.rotate`. Registering it as `"conveyor.rotate"` makes the lookup ask for
`conveyor.rotate.rotate`, which matches nothing — the part is placed, draws
correctly, and silently does nothing.

**3. A belt cannot wake a sleeping carton.** If your part *holds* product —
a stop, a gate, anything a carton can come to rest against — know that a belt
drives its load through `ConstantLinearVelocity`, which is a **surface**
velocity acting through contact friction, and contact friction does nothing to a
body the solver has put to sleep. `BoxPhysics` therefore sets
`CanSleep = false`; without it, anything held stationary on a belt slept after a
second or two and the belt underneath it could never restart it. Do not undo
that as an optimisation.

**4. Anything read in `_Ready` must also be saved.** Parts build their geometry
from their exported values, so a setting your part does not put into
`CaptureSettings` is lost the moment the scene is saved and reloaded. The
converse bites too: a value the part *recomputes* every tick is not a setting,
and saving it stores a sample and restores it as configuration. See Step 1.

---

> **Everything a part must touch, in one list.** Two things, since HP-34:
>
> | Where | What it does |
> |---|---|
> | `engine/src/Parts/<YourPart>.cs` | the machine, and everything about it: its tags, its settings, its tick, its inspector rows, its reset, what a click on it means |
> | `PartCatalog.All` | one entry: palette button, group, tooltip, and the factory that builds it |
>
> That is the whole list. It used to be eight, and the two extra entries nobody
> remembers are worth naming because they are what the drift actually looked
> like: a part's tags were registered in one file and dispatched in another, so
> the weighing conveyor shipped with a `.fault` tag that appeared in the
> inspector, could be forced, and stopped nothing. A suffix table and an
> operate-mode table in a third file held per-part metadata that no longer had
> anywhere to drift *from*.
>
> `--self-test=partcontract` fails if a concrete part type name appears anywhere
> in `engine/src/Editor/` outside those two places, so the list cannot quietly
> grow back. `--self-test=scene` walks the catalog and checks every part
> round-trips through a save and a load. `--self-test=newparts` and
> `--self-test=lineparts` are the pattern for behaviour: assert the *effect*,
> not that the tag exists.

---

## 📝 Step-by-Step Part Creation

### Step 1: Write the part

One file under `engine/src/Parts/`. It is a Godot `Node3D` (or a `StaticBody3D`,
or an `Area3D` — whatever the machine needs) that implements `IPart`.

Every member of `IPart` except `DeclareTags` has a default, so a part with no
settings and nothing to do each tick says so by staying silent:

```csharp
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

public partial class CustomPart : Node3D, IPart
{
    [Export] public float Speed { get; set; } = 1.0f;

    public bool IsActive { get; private set; }

    public override void _Ready()
    {
        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.4f, 0.4f, 0.4f) },
            MaterialOverride = new StandardMaterial3D { AlbedoColor = new Color(0.2f, 0.6f, 0.9f) },
        });
    }

    // What your PLC sees. The instance id is a tag PREFIX, so you declare
    // "run" and the builder makes "<id>.run" out of it.
    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("run", $"Custom {tags.Index} Run", TagKind.Output)
        .Bit("active", $"Custom {tags.Index} Active", TagKind.Input);

    // One physics tick. `tick.Dt` is scaled simulation time, so your part obeys
    // pause and the time-scale control for free.
    public void StepPart(PartTick tick)
    {
        IsActive = tick.Bit("run");
        tick.Write("active", IsActive);
    }

    // Settings the scene file has to carry, or they revert on load.
    public void CaptureSettings(PartSettings settings) => settings.Put("speed", Speed);

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("speed") is { } speed) Speed = speed;
    }

    // Rows in the property inspector.
    public void DescribeControls(IPartInspector ui) =>
        ui.Slider("Speed (m/s)", Speed, 0.1f, 2.0f, 0.1f, value => Speed = value);
}
```

Four things about that code are not obvious:

**`DeclareTags` must not depend on `_Ready`.** The catalog builds one throwaway
instance and asks it what I/O it has, to fill the palette tooltip and to build
the dispatch's id cache. A declaration that reads geometry `_Ready` has not
built yet will be wrong there and right everywhere else, which is the worst
possible combination.

**A value you *compute* every tick is not a setting.** Saving it stores a sample
and restores it as configuration. `VariableConveyor` is the worked example: the
drive recomputes `Speed` from the reference on every tick, so it overrides
`SpeedIsSetting` to false, `CaptureSettings` skips it, the inspector does not
offer a slider for it, and `--self-test=scene` asserts its absence from a saved
file.

**`ApplySettings` runs before the node enters the tree**, precisely because
`_Ready` builds geometry from these values. The trap is the other way round: a
`_Ready` that assigns a default *unconditionally* overwrites what was just
restored, and the part then reloads at its defaults with the correct values sat
in the scene file.

**Every inspector row must reach the simulation.** If the value is only read
while the part is being built, give your part a `Rebuild()` and call it inside
the lambda — `val => { CurtainHeight = val; Rebuild(); }` — or the slider moves
and nothing happens. That is not a hypothetical: `LightArray`'s "Curtain Height"
shipped exactly that way.

---

### Step 2: Add a catalog entry

`engine/src/Editor/PartCatalog.cs`. One entry gives you the palette button, its
group, its tooltip, the factory the editor builds your part with, and a place in
`--self-test=scene`'s save/load round-trip:

```csharp
new("CustomPart", "Custom Part", "PROCESS",
    "One sentence on what this does and why somebody would place it.",
    () => new CustomPart { Speed = 1.5f }),
```

The summary shows in the palette's tooltip *and* under the part's name in the
property inspector. Write it for somebody who has not read this guide. The tag
list beside it is **not** written here — it is read off your `DeclareTags`, so a
tooltip cannot promise I/O your part does not have.

The factory is the place to put whatever defaults make the part sensible the
moment it lands from the palette. It lives beside the description rather than in
a switch in the editor because the two could disagree, and a palette button that
places nothing is what that disagreement looks like.

---

### Step 3: Assert the behaviour, not the tags

That is the whole build. Now prove it does something.

`--self-test=scene` already covers the save/load round-trip from the catalog
entry alone, and `--self-test=partcontract` already checks nothing outside your
file learned your type's name. Neither of them checks your machine *works*.

Add a check to `--self-test=newparts`, `--self-test=lineparts` or
`--self-test=controlparts` (or a new self-test of your own) that asserts an
**effect**: not "the tag exists" but
"forcing `.fault` stopped the belt", "the reference ramped and `actual` lagged
it", "a raised blade held a carton on a belt that was still running". A test
that passes while the simulation does nothing is not a test, and it is
surprisingly easy to write one by accident.

Then prove the test: reintroduce the bug and watch it fail. Gotcha 24 in
`AGENTS.md` exists because a correct hit test and a correct pulse dispatch
coexisted with a panel that was completely dead from a user's seat.

If your part's settings appear in one of the templates `--self-test=partsettings`
drives, add its rows to that test's `Covered` map with a check per row — it fails
on a settings row it does not know how to drive:

```
FAIL  CustomPart: settings row 'Speed (m/s)' has no check in PartSettingsSelfTest
```

Assert a **named observable** there — what a person would see change — rather
than that the property was assigned, which is true by construction and proves
nothing. That test also fails a row wider than the panel's 260px content width:
the panel is fixed-width with horizontal scrolling off, so an over-wide row does
not scroll, it pushes the whole panel off the right of the screen. Keep labels
short, and watch controls that size themselves to their content — an
`OptionButton` takes the width of its longest *menu item* unless you set
`FitToLongestItem = false`.

---

### Step 4: The rest of `IPart`, as you need it

| Member | When you need it |
|---|---|
| `ResetPart(PartReset)` | your part accumulates something during a run — a temperature, a count, a latched state. A reset restarts the run; it does not undo the scene somebody built. Until this existed the heating station kept its heat across a reset, and the next run started from a place no experiment could reproduce. |
| `PrefixRenamed(oldId, newId)` | your part stores one of its own tag ids as a string. Renaming moves the tags, and a stored id would point at a tag that no longer exists. |
| `Operation` / `HitTestRegion` / `Operate` | a human can click your part. See Step 7. |
| `IDialPart` | your part has a knob that is dragged rather than clicked. See Step 8. |

---

### Step 5: Parts the operator can touch (optional)

Most parts only ever read tags. If yours has controls a human should be able to
click — buttons, selector switches, a hand valve — three extra things apply.
`engine/src/Parts/ButtonPanel.cs` is the worked example.

**Register the controls as `TagKind.Input`.** The kind is from the *controller's*
point of view: the operator drives the button, the controller reads it, so it is
an input exactly like a sensor.

**Say how a click reaches you, and what it means.** Three members of `IPart`:

```csharp
// The noun is for Run mode's "N parts respond to a click" banner. Naming it
// here is what stops a new operable part being one the banner never mentions.
public PartOperation? Operation => new("custom machine", "run");

public void Operate(PartOperate op) => op.ToggleBit("run");
```

`PartOperate` has `ToggleBit` for a contact, `ToggleAnalog` for a percentage (a
click means fully open or fully shut), `PulseBit` for something that means a
rising edge, and `Force` for the cases that need two tags moved at once — a fan
needs an enable *and* a reference, or the speed goes to 100 % and nothing turns.
You can also just drive the part directly and let it publish its own state next
tick: a selector switch steps round a detent, a guard door slides or refuses.
Writing the tag from the click instead would make the click and the machine two
authorities for one value.

**Hit-test your own geometry, not your bounding box.** If your part has more
than one control, say `Precise: true` and answer `HitTestRegion`, because a
part's box also covers its housing and its pedestal — hit-testing that would
make the whole station one big Start button, and for the two-hand station it
would put both palms under one click, which is precisely the defeat that part
exists to refuse.

```csharp
public PartOperation? Operation => new("custom machine", Precise: true);

public string? HitTestRegion(Vector3 from, Vector3 direction)
{
    var toLocal = GlobalTransform.AffineInverse();
    Vector3 origin = toLocal * from;
    Vector3 dir = (toLocal.Basis * direction).Normalized();
    // ...test each control in local space, return its name, or null for a miss
}

public void Operate(PartOperate op) => op.ToggleBit(op.Region);
```

Take the ray into local space. A test written against world axes passes for an
unrotated part and misses every control once someone turns it.

**Decide momentary or maintained, and mean it.** A momentary contact is high for
*one scan*, not for as long as the mouse is down — clicks arrive on the frame
clock and tags are written on the physics clock, so queue presses when the click
lands and drain the queue in your `StepPart`, clearing the previous tick's pulse
before raising this tick's. `ButtonPanel` does it in about eight lines. Keep the
pending list as *suffixes*, not full tag ids: a rename moves the tags, and a
stored id would strand a pulse latched high under the new name. A maintained
control latches until it is clicked again.
Getting this wrong produces a button that looks fine and gives a PLC program a
rising edge of unpredictable width.

Finally: **the input path needs its own test.** Take the click position from the
`InputEventMouseButton`, never from `GetViewport().GetMousePosition()`, and
verify with `--self-test=click`. The logic can be entirely correct while
clicking does nothing. That is not hypothetical twice over: Run mode's dispatch
had exactly this bug, and so did Edit mode's selection, found only when a drag
needed to grab the part under the *press* rather than under wherever the cursor
had wandered.

---

### Step 6: Controls that are dragged, not clicked (optional)

The setpoint pot on `ButtonPanel` is the worked example, and it needed three
things a button does not.

**Its own region, reported from the same hit test.** Implement `IDialPart` and
return `PartOperate.DialRegion` from `HitTestRegion` when the ray lands on the
knob. A cap is pressed and a pot is turned; the click dispatch returns early for
that region, so grabbing the knob cannot also fire whichever cap the enum
happens to default to.

**Join the shared hit test, not a private one.** `SceneEditor.FindOperableTarget`
is what both the click dispatch and the hover highlight ask, precisely so the two
can never disagree about what the cursor is over. A control reachable only
through its own second ray cast is a control the hover cannot know about — and
one nobody will find, because nothing on screen reacts as the mouse passes over
it. The cursor shape is the affordance for a drag; the outline is the affordance
for a click.

**Say so when the mode is entered.** Run mode's banner reads *"N parts respond to
a click"*, which is exactly the sentence that leaves a drag-only control
undiscovered. If your part has one, the banner has to mention it.

---

### Step 7: Parts that can fail (optional)

Anything with a drive should be able to break. Until every actuator could, a
command and reality could never disagree in this library — and an interlock
exists precisely because the plant does not always obey.

**Declare a `.fault` tag as `TagKind.Input`.** Nothing in the simulation
computes it; it is raised by the toolbar's fault tool, a forced tag or a test,
and read by the controller exactly like a sensor. `PartCatalog.CanFault` derives
"can this break?" from your `DeclareTags` rather than from a second list, so
declaring the tag is the whole opt-in.

**Dispatch it in your own `StepPart`, first.** This is the half that used to go
missing, and it is worth knowing why it cannot any more. The weighing conveyor
declared `.fault` exactly as every other conveyor did, in one file, and the
per-tick dispatch that acted on it lived in another — so the tag existed,
appeared in the inspector, could be forced, and stopped nothing. Both halves are
now in your part, a few lines apart.

**The fault must win over the command, in one place.** Resolve it at the top of
whatever applies the command:

```csharp
public void SetRunning(bool running)
{
    if (IsFaulted) running = false;   // one place the two can disagree
    // ...
}
```

**Fail where it stands, not where it is safe.** A jammed cylinder stops
mid-stroke and stays there; a seized valve holds its opening. "Returns home" and
"fails closed" are *safe* failures, and safe failures teach nothing — the whole
point is that the limit switches, or the process variable, become the only
honest thing to read.

**Show it.** A stopped machine and a faulted machine look identical, and the
difference is the whole diagnosis. Every faultable part carries a beacon on a
short stalk, dark until it lights.

**Watch what you name.** `--self-test=roller` took *"the first child whose mesh
is a `CylinderMesh`"* to mean "a roller", so a fault beacon mounted on a
cylindrical stalk became the first cylinder and the test started measuring a lamp
post for rotation. Name the meshes that matter, and look them up by name.
