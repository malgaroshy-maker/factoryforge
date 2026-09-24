using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that the seven parts added in CP-01…CP-07 actually do what
/// their tags claim:
///
/// <code>godot --headless --path engine -- --self-test=newparts</code>
///
/// Placing a part and seeing it register tags proves nothing — that was true of
/// the F4 wiring panel too, and it configured nothing for weeks. What is
/// asserted here is the *effect*: that the drive's actual speed lags its
/// reference rather than snapping to it, that the diverter's two limit switches
/// are mutually exclusive mid-sweep, that the gantry reports a position it
/// actually travelled to, that the scanner's read pulse is one edge held long
/// enough to be polled and does not repeat for the same item, that the gauge's needle moves, that the
/// beacon lights, and that the heater has a real time constant and a fault that
/// the command cannot reveal.
/// </summary>
public partial class NewPartsSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private int _step;
    private bool _done;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private const double Tick = 1.0 / 60.0;

    private void Run(int ticks)
    {
        for (int i = 0; i < ticks; i++) Editor._PhysicsProcess(Tick);
    }

    private double Num(string id) => System.Convert.ToDouble(Tags.Visible(id));
    private bool Bit(string id) => Tags.Contains(id) && Tags.Visible(id) is true;

    private T Part<T>(string id) where T : Node3D
    {
        var node = Editor.NodeFor(id) as T;
        if (node is null) throw new System.InvalidOperationException($"'{id}' is not a {typeof(T).Name}");
        return node;
    }

    /// <summary>
    /// Phases, and why they cannot collapse into one tick.
    ///
    /// Most of what is checked here can be driven by calling the editor's
    /// dispatch directly, which is what <see cref="Run"/> does — a hand-turned
    /// clock, so a 90-second thermal ramp costs no wall time. The scanner is
    /// the exception: it reads an <c>Area3D</c>, and area overlaps are resolved
    /// by the physics *server*, which only advances on real engine ticks. A
    /// hand-turned clock moves the dispatch and not the world, so a carton
    /// dropped and read in the same burst is never seen at all. That cost an
    /// hour the first time; hence the box goes in at step 4 and is asked about
    /// at step 12, with real ticks in between.
    /// </summary>
    private bool _sawReadPulse;

    /// <summary>Ticks the read output was seen on during the real-tick
    /// window, before CheckScanner takes over on the hand-turned clock.</summary>
    private int _readHighTicks;

    public override void _PhysicsProcess(double delta)
    {
        // Two ticks of grace: parts build their geometry in _Ready, which does
        // not run until the node has been in the tree for a frame, and half of
        // what is asserted below reads that geometry back.
        if (++_step == 2) { BuildScene(); return; }
        if (_step == 4) { StartScannerProbe(); return; }
        if (_step > 4 && _step < 12)
        {
            // The self-test runs before the editor each tick (it is added to
            // the tree first), so this sees the previous tick's dispatch. A
            // sticky flag is the right shape for that; an equality check on one
            // chosen tick would not be.
            if (Bit("barcodescanner.read")) { _sawReadPulse = true; _readHighTicks++; }
            return;
        }
        if (_step > 12)
        {
            // The diverter's physical half, on real ticks: a blade that only
            // turns in the node tree deflects nothing, and the hand-turned
            // clock above cannot tell the difference.
            if (_done) return;
            try
            {
                if (!StepDiverterRig()) return;
            }
            catch (System.Exception ex)
            {
                _failures.Add(ex.Message);
                GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
            }
            _done = true;
            Report();
            return;
        }
        if (_step != 12) return;

        try
        {
            // The scanner first: it measures how long the read output stayed
            // on, counting on from the real ticks above, and every other check
            // turns the editor's clock by hand -- which would run that hold
            // down unobserved.
            CheckScanner();
            CheckVariableDrive();
            CheckPivotDiverter();
            CheckGantry();
            CheckGauge();
            CheckBeacon();
            CheckHeater();
            CheckSelector();
            CheckGate();
        }
        catch (System.Exception ex)
        {
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }
    }

    private void Report()
    {
        if (_failures.Count == 0)
        {
            GD.Print("self-test newparts: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test newparts: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private void BuildScene()
    {
        var data = new SceneData { Name = "selftest-new-parts" };
        var types = new[]
        {
            "VariableConveyor", "PivotDiverter", "PickPlaceArm",
            "BarcodeScanner", "AnalogGauge", "AlarmBeacon", "HeatingStation",
            "SelectorSwitch", "SafetyGate",
        };

        for (int i = 0; i < types.Length; i++)
        {
            data.Parts.Add(new PartInstanceData
            {
                Id = types[i].ToLowerInvariant(),
                Type = types[i],
                Position = new[] { -5.0f + i * 1.6f, 0.5f, -3.0f },
                Rotation = new[] { 0.0f, 0.0f, 0.0f },
            });
        }

        // The diverter rig, well clear of the row above, laid out the way the
        // light-curtain cell lays out its pusher: a running belt, the diverter
        // one grid cell off its centre line on the near side, and a chute one
        // cell off on the far side. The diverter takes every default -- this is
        // the part as the palette places it, not one tuned to pass.
        data.Parts.Add(new PartInstanceData
        {
            Id = "dvbelt", Type = "ConveyorBelt",
            Position = new[] { 0.0f, PartLayout.WorkPlaneY, RigZ },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
            Properties = new Dictionary<string, string>
            {
                ["size_x"] = "3", ["size_y"] = "0.12", ["size_z"] = "0.5", ["speed"] = "0.5",
            },
        });
        data.Parts.Add(new PartInstanceData
        {
            Id = "dv", Type = "PivotDiverter",
            Position = new[] { RigPivotX, PartLayout.WorkPlaneY, RigZ - 0.5f },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
        });
        // Wide, because a turned carton leaves the belt moving downstream as
        // well as across. And with its lip dropped: at the chute's default
        // 0.015 m it is the slab's *centre line* that sits below the belt, and a
        // 0.04 m slab tilted 30° brings its top edge up to 2 mm *above* the belt
        // surface. A pusher's shove climbs that; a carton that only the belt's
        // friction moves along the blade stops dead against it, square on the
        // lip, which this rig first misread as the diverter failing.
        data.Parts.Add(new PartInstanceData
        {
            Id = "dvchute", Type = "Chute",
            Position = new[] { RigPivotX + 0.5f, PartLayout.WorkPlaneY, RigZ + 0.5f },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
            Properties = new Dictionary<string, string> { ["ramp_width"] = "1.4", ["lip_drop"] = "0.04" },
        });

        const string path = "user://selftest_newparts.json";
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Editor.LoadSceneFromFile(path);
    }

    /// <summary>The whole reason this part is not just another belt: the
    /// reference and the actual are two different numbers while the ramp
    /// runs. A drive whose actual equalled its reference on the first tick
    /// would be a bit output wearing a float's clothes.</summary>
    private void CheckVariableDrive()
    {
        const string id = "variableconveyor";
        Tags.Set($"{id}.run", true);
        Tags.Set($"{id}.speed", 100.0);

        Run(1);
        double afterOne = Num($"{id}.actual");
        Expect(afterOne > 0.0 && afterOne < 100.0,
               $"VFD actual lags its reference on the first tick (got {afterOne:0.00})");

        Run(240);
        double settled = Num($"{id}.actual");
        Expect(settled > 99.0, $"VFD actual reaches the reference given time (got {settled:0.0})");
        Expect(Part<VariableConveyor>(id).IsRunning, "VFD belt is running at speed");

        // The fault has to beat the command, and it has to be visible in the
        // measurement rather than only in the belt's own state -- a controller
        // watching `actual` is how a real one notices.
        Tags.Force($"{id}.fault", true);
        Run(240);
        Expect(Num($"{id}.actual") < 1.0,
               "a faulted VFD coasts to zero while `run` and `speed` are still commanded");
        Expect(Bit($"{id}.run"), "the run command is still on while the drive is faulted");
        Tags.ClearForce($"{id}.fault");
    }

    private void CheckPivotDiverter()
    {
        const string id = "pivotdiverter";
        Expect(Bit($"{id}.home"), "a placed diverter starts parked");
        Expect(!Bit($"{id}.diverted"), "a parked diverter is not also diverted");

        Tags.Set($"{id}.divert", true);
        Run(2);
        Expect(!Bit($"{id}.home") && !Bit($"{id}.diverted"),
               "mid-sweep the diverter is at neither limit — the state a sequence has to wait through");

        Run(120);
        Expect(Bit($"{id}.diverted"), "the diverter reaches its limit");

        // Seize it mid-return: the arm must stay where it is, not creep home.
        Tags.Set($"{id}.divert", false);
        Run(6);
        Tags.Force($"{id}.fault", true);
        float frozen = Part<PivotDiverter>(id).Angle;
        Run(120);
        Expect(Mathf.IsEqualApprox(Part<PivotDiverter>(id).Angle, frozen),
               $"a seized diverter freezes mid-sweep (was {frozen:0.0}°, now {Part<PivotDiverter>(id).Angle:0.0}°)");
        Tags.ClearForce($"{id}.fault");

        Run(240);
        Expect(Bit($"{id}.home"), "clearing the fault lets the diverter finish going home");
    }

    // ---------- IP-32: the blade has to turn cartons, not just report it

    /// <summary>The rig's lane centre, and the diverter's x along it. The belt
    /// runs +X from x = -1.5 to 1.5; the diverter's post is just off its near
    /// edge (z ≈ RigZ - 0.26), the blade parked downstream along that edge,
    /// and it sweeps across towards the far one.</summary>
    private const float RigZ = 5.0f;
    private const float RigPivotX = 0.0f;

    /// <summary>The belt's far edge. A carton whose centre is well past this
    /// has left the lane.</summary>
    private const float RigFarEdge = RigZ + 0.25f;

    /// <summary>Where the parked blade ends (pivot + the default blade
    /// length). A carton past this has gone by the diverter.</summary>
    private const float RigBladeEnd = RigPivotX + PivotDiverter.DefaultBladeLength;

    private const int RigTimeout = 480;

    private int _rigPhase;
    private int _rigTicks;
    private BoxPhysics? _rigBox;
    private float _rigMinZ, _rigMaxZ;

    private BoxPhysics DropRigCarton(bool tall)
    {
        var box = new BoxPhysics { IsTall = tall };
        Editor.GetParent().AddChild(box);
        box.GlobalPosition = new Vector3(-1.2f, PartLayout.WorkPlaneY + PartLayout.BeltSurface + box.Height / 2.0f + 0.02f, RigZ);
        _rigMinZ = _rigMaxZ = RigZ;
        _rigTicks = 0;
        return box;
    }

    private static string Where(Node3D n) =>
        $"({n.GlobalPosition.X:0.00}, {n.GlobalPosition.Y:0.00}, {n.GlobalPosition.Z:0.00})";

    /// <summary>
    /// One real tick of the diverter rig. Returns true when it is finished.
    ///
    /// Phase 1, home: a carton rides the whole lane past the parked blade and
    /// is not turned. Phase 2: the blade swings across and the collider — the
    /// physics server's body, not the drawn node — has to be at the divert
    /// angle. Phase 3 and 4, divert: a short and then a tall carton are turned
    /// off the near edge onto the chute, with the belt running throughout.
    /// </summary>
    private bool StepDiverterRig()
    {
        var dv = Part<PivotDiverter>("dv");
        _rigTicks++;

        switch (_rigPhase)
        {
            case 0:
                Tags.Set("dvbelt.rotate", true);
                Tags.Set("dv.divert", false);
                _rigBox = DropRigCarton(tall: false);
                _rigPhase = 1;
                return false;

            case 1:
            {
                var box = _rigBox!;
                _rigMinZ = Mathf.Min(_rigMinZ, box.GlobalPosition.Z);
                _rigMaxZ = Mathf.Max(_rigMaxZ, box.GlobalPosition.Z);
                if (box.GlobalPosition.X < RigBladeEnd + 0.05f && _rigTicks < RigTimeout) return false;

                GD.Print($"  rig home: carton at {Where(box)} after {_rigTicks} ticks, z in [{_rigMinZ:0.00}, {_rigMaxZ:0.00}]");
                Expect(box.GlobalPosition.X >= RigBladeEnd + 0.05f,
                       $"at `home` a carton rides past the parked diverter (at {Where(box)} after {_rigTicks} ticks)");
                Expect(_rigMinZ > RigZ - 0.08f && _rigMaxZ < RigZ + 0.08f,
                       $"and goes straight by it: z stayed in [{_rigMinZ:0.00}, {_rigMaxZ:0.00}] about the lane centre {RigZ:0.00}");
                Expect(Mathf.Abs(dv.BladeColliderAngle) < 1.0f,
                       $"the parked blade's collider is parked (at {dv.BladeColliderAngle:0.0}°)");
                box.QueueFree();
                _rigBox = null;

                Tags.Set("dv.divert", true);
                _rigTicks = 0;
                _rigPhase = 2;
                return false;
            }

            case 2:
                if (!Bit("dv.diverted") || _rigTicks < 30) return _rigTicks > RigTimeout && Fail("the rig diverter never reported `diverted`");
                // The reported angle and the collider's must agree. This is
                // the check IP-32 was missing: the part said `diverted` while
                // its collider was still parked along the edge.
                GD.Print($"  rig divert: reported {dv.Angle:0.0}°, collider {dv.BladeColliderAngle:0.0}°, "
                         + $"collider origin {dv.BladeColliderLocal.Origin}");
                Expect(Mathf.Abs(dv.BladeColliderAngle - dv.DivertAngle) < 1.0f,
                       $"with `diverted` made, the blade's collider is across the lane at {dv.DivertAngle:0}° "
                       + $"(it is at {dv.BladeColliderAngle:0.0}°; the drawn blade is at {dv.Angle:0.0}°)");
                _rigBox = DropRigCarton(tall: false);
                _rigPhase = 3;
                return false;

            case 3:
            case 4:
            {
                var box = _rigBox!;
                string what = _rigPhase == 3 ? "short" : "tall";
                if (_rigTicks % 60 == 0)
                    GD.Print($"  rig {what}: t={_rigTicks} at {Where(box)} yaw={Mathf.RadToDeg(box.GlobalRotation.Y):0.0}");
                bool off = box.GlobalPosition.Z > RigFarEdge + 0.1f;
                if (!off && _rigTicks < RigTimeout) return false;

                GD.Print($"  rig {what}: {(off ? "diverted" : "NOT diverted")} at {Where(box)} after {_rigTicks} ticks");
                Expect(off,
                       $"at `divert` a {what} carton on the running belt is turned off the far edge "
                       + $"(z > {RigFarEdge + 0.1f:0.00}); it is at {Where(box)} after {_rigTicks} ticks");
                Expect(!off || box.GlobalPosition.X < RigBladeEnd + 0.4f,
                       $"and it is turned at the blade, not somewhere past it (at {Where(box)})");
                box.QueueFree();
                if (_rigPhase == 3)
                {
                    _rigBox = DropRigCarton(tall: true);
                    _rigPhase = 4;
                    return false;
                }
                Tags.Set("dvbelt.rotate", false);
                Tags.Set("dv.divert", false);
                return true;
            }
        }
        return true;
    }

    private bool Fail(string what)
    {
        Expect(false, what);
        return true;
    }

    private void CheckGantry()
    {
        const string id = "pickplacearm";
        Expect(Bit($"{id}.raised"), "a placed gantry starts raised");
        Expect(!Bit($"{id}.holding"), "a placed gantry is not holding anything");

        Tags.Set($"{id}.target", 80.0);
        Run(2);
        Expect(!Bit($"{id}.inposition"),
               "the gantry is out of position the moment a new target is given");

        Run(300);
        Expect(Mathf.Abs(Num($"{id}.position") - 80.0) < 2.0,
               $"the gantry travels to its target (got {Num($"{id}.position"):0.0} %)");
        Expect(Bit($"{id}.inposition"), "and says so");

        Tags.Set($"{id}.lower", true);
        Run(120);
        Expect(Bit($"{id}.lowered") && !Bit($"{id}.raised"),
               "the Z column reaches its bottom limit and leaves the top one");

        // Gripping thin air must not claim a hold. A machine that reports a
        // successful pick it did not make is worse than one that fails.
        Tags.Set($"{id}.grip", true);
        Run(4);
        Expect(!Bit($"{id}.holding"),
               "gripping where there is nothing leaves `holding` false rather than lying");

        Tags.Set($"{id}.grip", false);
        Tags.Set($"{id}.lower", false);
        Run(120);
        Expect(Bit($"{id}.raised"), "the column returns to its top limit");
    }

    private BoxPhysics? _probeBox;

    /// <summary>Park a carton under the scanner head. Frozen kinematic so it
    /// stays put across the real ticks the physics server needs to notice the
    /// overlap, rather than falling out of the window before it is asked
    /// about.</summary>
    private void StartScannerProbe()
    {
        const string id = "barcodescanner";
        Expect(Bit($"{id}.enable"), "a placed scanner is armed");
        Expect(!Bit($"{id}.present"), "an empty read window reports nothing present");
        Expect(!Bit($"{id}.read"), "and pulses nothing");

        var scanner = Part<BarcodeScanner>(id);
        _probeBox = new BoxPhysics { IsTall = true };
        Editor.GetParent().AddChild(_probeBox);
        _probeBox.GlobalPosition = scanner.GlobalPosition
                                   + new Vector3(0, PartLayout.BeltSurface + 0.16f, 0);
        _probeBox.FreezeMode = RigidBody3D.FreezeModeEnum.Kinematic;
        _probeBox.Freeze = true;
    }

    private void CheckScanner()
    {
        const string id = "barcodescanner";

        Expect(Bit($"{id}.present"), "a carton in the window is reported present");
        Expect((int)Num($"{id}.code") == BarcodeScanner.CodeTallCarton,
               $"a tall carton reads as {BarcodeScanner.CodeTallCarton} (got {(int)Num($"{id}.code")})");
        Expect(_sawReadPulse, "the scanner pulsed `read` when the carton arrived");

        // One edge per item, held long enough for a polled link to see
        // (IP-31) -- it used to be one scan wide, and a 50 ms Modbus poll saw
        // about one read in three -- and never repeated for the same item,
        // which is what forces a program to latch the code on the edge.
        var scanner = Part<BarcodeScanner>(id);
        bool prev = Bit($"{id}.read");
        int high = _readHighTicks + (prev ? 1 : 0);
        int edges = 0;
        for (int i = 0; i < 60; i++)
        {
            Editor._PhysicsProcess(Tick);
            bool now = Bit($"{id}.read");
            if (now) high++;
            if (now && !prev) edges++;
            prev = now;
        }
        Expect(edges == 0,
               $"the read pulse does not repeat while the same item sits under the head (saw {edges} more edges)");
        int minTicks = Mathf.CeilToInt(0.2f * Engine.PhysicsTicksPerSecond - 1e-3f);
        Expect(high == scanner.ReadHoldTicks && high >= minTicks,
               $"the read output stays on for its hold, at least 0.2 s = {minTicks} ticks (on for {high})");
        Expect(!Bit($"{id}.read"), "and is off again once the hold has run out");

        _probeBox?.QueueFree();
        _probeBox = null;
    }

    private void CheckGauge()
    {
        const string id = "analoggauge";
        var gauge = Part<AnalogGauge>(id);

        Tags.Set($"{id}.value", 0.0);
        Run(2);
        float atZero = gauge.RotationOfNeedleDegrees;

        Tags.Set($"{id}.value", 75.0);
        Run(2);
        Expect(Mathf.IsEqualApprox(gauge.Value, 75.0f), "the gauge takes the value it is given");
        Expect(!Mathf.IsEqualApprox(gauge.RotationOfNeedleDegrees, atZero),
               "and the needle actually moves — a dial that only changes its digits is a display, not a gauge");

        Tags.Set($"{id}.value", 99.0);
        Run(2);
        Expect(gauge.InAlarm, "a reading above the alarm point is in the red band");
    }

    private void CheckBeacon()
    {
        const string id = "alarmbeacon";
        var beacon = Part<AlarmBeacon>(id);

        Tags.Set($"{id}.beacon", true);
        Tags.Set($"{id}.horn", true);
        Run(2);
        Expect(beacon.BeaconOn && beacon.HornOn, "the beacon and horn follow their tags");

        Tags.Set($"{id}.beacon", false);
        Run(2);
        Expect(!beacon.BeaconOn && beacon.HornOn, "and are independent of each other");
        Tags.Set($"{id}.horn", false);
        Run(2);
    }

    /// <summary>The third kind of operator input: it stays where it is put.
    /// A selector that reverted, or that published an edge instead of a
    /// position, would be a second Start button with a knob on it.</summary>
    private void CheckSelector()
    {
        const string id = "selectorswitch";
        var selector = Part<SelectorSwitch>(id);

        Run(2);
        int start = (int)Num($"{id}.position");
        Expect(start == selector.Detent, "the selector publishes the detent it is in");

        selector.Advance();
        Run(2);
        Expect((int)Num($"{id}.position") == selector.Detent
               && selector.Detent != start,
               "advancing it moves the published position");

        // Maintained, not momentary: it must still read the same many ticks
        // later, with nobody touching it.
        int held = selector.Detent;
        Run(120);
        Expect((int)Num($"{id}.position") == held,
               $"and it stays there ({held} -> {(int)Num($"{id}.position")})");

        // Wrapping rather than clamping, so a click can always reach every
        // position on a part whose only gesture is one click.
        for (int i = 0; i < selector.PositionCount; i++) selector.Advance();
        Run(2);
        Expect(selector.Detent == held, "a full turn of the detents comes back where it started");
    }

    /// <summary>The guard's whole point is that the lock is a two-way
    /// contract: the controller decides whether the door may be opened, and
    /// while it says no, pulling the handle does nothing.</summary>
    private void CheckGate()
    {
        const string id = "safetygate";
        var gate = Part<SafetyGate>(id);

        Run(2);
        Expect(Bit($"{id}.closed"), "a placed guard starts shut, and its switch reads closed");

        gate.Toggle();
        Run(120);
        Expect(!Bit($"{id}.closed"),
               "opening the guard opens the switch — wired NC, so open reads as not safe");

        gate.Toggle();
        Run(120);
        Expect(Bit($"{id}.closed"), "and shutting it closes the switch again");

        // Locked shut: the handle is refused, silently, exactly as a real
        // solenoid-locked gate refuses it.
        Tags.Set($"{id}.lock", true);
        Run(2);
        Expect(Bit($"{id}.locked"), "a locked, shut guard reports itself locked");
        gate.Toggle();
        Run(120);
        Expect(Bit($"{id}.closed"),
               "and will not open while the solenoid holds it — the refusal is the point");

        Tags.Set($"{id}.lock", false);
        Run(2);
        gate.Toggle();
        Run(120);
        Expect(!Bit($"{id}.closed"), "releasing the lock lets it open");
        gate.ResetGate();
        Run(2);
    }

    private void CheckHeater()
    {
        const string id = "heatingstation";
        var heater = Part<HeatingStation>(id);

        double ambient = Num($"{id}.temperature");
        Expect(Mathf.IsEqualApprox((float)ambient, heater.Ambient),
               "a placed heater starts at ambient");

        // A first-order plant does not arrive; that is the whole point. One
        // second of full power must move it, and must not finish it.
        Tags.Set($"{id}.heater", 100.0);
        Run(60);
        double afterOneSecond = Num($"{id}.temperature");
        Expect(afterOneSecond > ambient + 1.0,
               $"full power raises the temperature (got {afterOneSecond:0.0} °C)");
        Expect(afterOneSecond < heater.TargetTemp,
               "and does not reach setpoint in a second — the lag is the exercise");

        Run(60 * 90);
        Expect(Num($"{id}.temperature") > heater.TargetTemp - heater.Tolerance,
               $"and reaches it given time (got {Num($"{id}.temperature"):0.0} °C)");
        Expect(Bit($"{id}.attemp") || Num($"{id}.temperature") > heater.TargetTemp,
               "`attemp` reports the band it was configured with");

        // The failure that the command cannot reveal: power still reads 100 %
        // and the plate cools anyway.
        Tags.Force($"{id}.fault", true);
        double before = Num($"{id}.temperature");
        Run(60 * 20);
        Expect(Num($"{id}.temperature") < before - 5.0,
               "a failed element cools while the heater command still reads 100 %");
        Expect(Mathf.IsEqualApprox((float)Num($"{id}.heater"), 100.0f),
               "and the command really is still 100 % — only the measurement gives it away");
        Tags.ClearForce($"{id}.fault");
    }
}
