using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Drive every settings control the part property panel offers, and check each
/// one reaches the simulation (LE-11).
///
/// <code>godot --headless --path engine -- --self-test=partsettings</code>
///
/// <c>--self-test=proppanel</c> covers the panel's *I/O* half -- the toggles and
/// sliders that force tags. Nothing covered its *settings* half, and that is
/// where a real bug sat: "Curtain Height" moved a number
/// <see cref="LightArray"/> only ever read while building itself, so the slider
/// moved and the curtain did not (LE-01). The panel's own comment states the
/// rule ("anything whose value is only read when the part is built needs a
/// Rebuild()"), <c>docs/PART_AUTHORING.md</c> states it again for anyone adding
/// a part, and it was still broken in the shipped library.
///
/// So this test asserts a **named observable** per control rather than that the
/// property was assigned -- assignment is true by construction and proves
/// nothing. It also checks the reverse direction: every settings row the panel
/// produces must be in <see cref="Covered"/>, so a row added later without a
/// check here fails rather than quietly joining the untested set.
/// </summary>
public partial class PartSettingsSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    /// <summary>Every settings row this test knows how to drive, by part type.
    /// A row on screen that is not here is a failure, not an omission.</summary>
    private static readonly Dictionary<string, string[]> Covered = new()
    {
        ["ConveyorBelt"] = new[] { "Belt Speed (m/s)", "Surface Friction" },
        ["RollerConveyor"] = new[] { "Belt Speed (m/s)", "Surface Friction" },
        ["WeighingConveyor"] = new[] { "Belt Speed (m/s)", "Surface Friction", "Signal", "Range Min", "Range Max" },
        ["PhotoelectricSensor"] = new[] { "Beam Range (m)", "Beam Height (m)" },
        ["InductiveSensor"] = new[] { "Beam Range (m)", "Beam Height (m)" },
        ["RetroreflectiveSensor"] = new[] { "Beam Range (m)", "Beam Height (m)" },
        ["PusherMechanism"] = new[] { "Stroke Speed (m/s)", "Stroke Length (m)" },
        ["LightArray"] = new[] { "Curtain Height (m)", "Beams" },
        // Range Min/Max only appear once the Signal is raw (IP-16).
        ["LevelTank"] = new[] { "Fill Rate (%/s)", "Drain Rate (%/s)", "Signal", "Range Min", "Range Max" },
        ["Chute"] = new[] { "Incline (deg)", "Surface Friction" },
        ["Emitter"] = new[] { "Metal every Nth" },
        ["Remover"] = new[] { "Counts into" },
        ["ButtonPanel"] = new[] { "Scale Min", "Scale Max", "Scale Unit", "Setpoint" },
        // IP-17's five. Not in any template yet (IP-14 places them), so the
        // test builds a scene of its own for them.
        ["LimitSwitch"] = new[] { "Lever (m)", "Height (m)", "Bounce (ms)" },
        ["SolenoidValve"] = new[] { "Travel Time (s)" },
        ["StarDeltaStarter"] = new[] { "Full Load (A)", "Motor Load (%)", "Inertia (s)", "Signal", "Range Min", "Range Max" },
        // Raw only: Range Min/Max are offered from the start, and Signal has
        // no engineering option to choose.
        ["PressureTransmitter"] = new[] { "Supply (bar)", "Consumption (%)", "Signal", "Range Min", "Range Max" },
        ["ServoAxis"] = new[] { "Stroke (mm)", "Max Speed (mm/s)", "Accel (mm/s2)", "Window (mm)" },
    };

    /// <summary>The panel's ScrollContainer bound, from
    /// <c>PartPropertyInspectorUI._Ready</c>. Horizontal scrolling is disabled
    /// there, so a wider row does not scroll -- it grows the panel.</summary>
    private const float PanelContentWidth = 260.0f;

    private readonly List<string> _failures = new();
    private int _step;
    private PartPropertyInspectorUI _panel = null!;

    // Two checks span ticks: the belt's surface velocity and the pusher's
    // stroke are both written by their own _PhysicsProcess, not by the setter.
    private ConveyorBelt? _belt;
    private PusherMechanism? _pusher;
    private string _pusherExtendTag = "";

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _PhysicsProcess(double delta)
    {
        _step++;
        switch (_step)
        {
            case 1: Editor.LoadTemplate("res://templates/light_curtain_sorting.json"); return;
            case 3: EnsurePanel(); CheckLightCurtainScene(); return;
            // The belt writes ConstantLinearVelocity from its own
            // _PhysicsProcess, so its surface speed is only observable a tick
            // after the slider moves.
            case 5: CheckBeltSpeedArrived(); return;
            case 45: CheckPusherArrived(); return;

            case 47: Deselect(); Editor.LoadTemplate("res://templates/roller_line_weighing.json"); return;
            case 49: CheckRollerScene(); return;

            case 51: Deselect(); Editor.LoadTemplate("res://templates/start_stop_station.json"); return;
            case 53: CheckSensorScene(); return;

            case 55: Deselect(); Editor.LoadTemplate("res://templates/tank_level_control.json"); return;
            case 57: CheckTankScene(); CheckPanelScene(); return;

            case 59: Deselect(); LoadIndustrialScene(); return;
            case 61: CheckIndustrialScene(); Finish(); return;
        }
    }

    /// <summary>Headless, Editor.PropertyInspector is null and this test builds
    /// its own panel, which SceneEditor therefore never clears on a scene
    /// change. Without this the panel's live-refresh closures keep reading the
    /// previous template's tag ids every frame after the reload.</summary>
    private void Deselect() => _panel.InspectNode(null, "", "");

    private void EnsurePanel()
    {
        _panel = Editor.PropertyInspector!;
        if (_panel is null)
        {
            _panel = new PartPropertyInspectorUI { Name = "TestPropertyInspector", Editor = Editor };
            AddChild(_panel);
        }
    }

    // ---------- light_curtain_sorting: curtain, belt, chute, emitter, remover, pusher

    private void CheckLightCurtainScene()
    {
        var curtain = Find<LightArray>();
        var belt = Find<ConveyorBelt>();
        var chute = Find<Chute>();
        var emitter = Find<Emitter>();
        var remover = Find<Remover>();
        _pusher = Find<PusherMechanism>();

        if (curtain is null || belt is null || chute is null || emitter is null
            || remover is null || _pusher is null)
        {
            Expect(false, "light-curtain-sorting: not every expected part was found");
            Finish();
            return;
        }

        CheckCurtain(curtain);
        CheckBelt(belt);
        CheckChute(chute);
        CheckEmitter(emitter);
        CheckRemover(remover);
        StartPusher(_pusher);
    }

    /// <summary>The LE-01 regression. Both rows change geometry built once, so
    /// both must rebuild; the top beam and the beam count are what a person
    /// sees change.</summary>
    private void CheckCurtain(LightArray curtain)
    {
        Inspect(curtain, "height_gauge", "LightArray");

        float topBefore = TopBeamY(curtain);
        Drive("Curtain Height (m)", 0.90);
        float topAfter = TopBeamY(curtain);
        Expect(topAfter > topBefore + 0.30f,
               $"Curtain Height rebuilds the curtain: top beam {topBefore:0.###} -> {topAfter:0.###}");

        int beamsBefore = BeamCount(curtain);
        Drive("Beams", 5);
        int beamsAfter = BeamCount(curtain);
        Expect(beamsAfter == 5 && beamsAfter != beamsBefore,
               $"Beams rebuilds the curtain: {beamsBefore} -> {beamsAfter} raycasts, wanted 5");
    }

    /// <summary>Friction is applied live to the physics material on set; speed
    /// is read every tick, and shows up as the belt's own surface velocity.</summary>
    private void CheckBelt(ConveyorBelt belt)
    {
        Inspect(belt, "belt", "ConveyorBelt");

        Drive("Surface Friction", 1.25);
        Expect(belt.PhysicsMaterialOverride is { } mat && Mathf.IsEqualApprox(mat.Friction, 1.25f),
               $"Surface Friction reaches the physics material (got {belt.PhysicsMaterialOverride?.Friction})");

        // Checked two ticks later, in CheckBeltSpeedArrived.
        _belt = belt;
        Tags.Force("belt.rotate", true);
        Drive("Belt Speed (m/s)", 1.60);
    }

    private void CheckBeltSpeedArrived()
    {
        if (_belt is null) return;
        Expect(Mathf.IsEqualApprox(_belt.ConstantLinearVelocity.Length(), 1.60f),
               $"Belt Speed reaches the running belt's surface velocity " +
               $"(got {_belt.ConstantLinearVelocity.Length():0.###})");
        Tags.ClearForce("belt.rotate");
    }

    private void CheckChute(Chute chute)
    {
        Inspect(chute, "chute", "Chute");

        Transform3D deckBefore = FirstShapeTransform(chute);
        Drive("Incline (deg)", 50.0);
        Expect(FirstShapeTransform(chute) != deckBefore,
               "Incline rebuilds the ramp's collision deck");

        Drive("Surface Friction", 0.80);
        Expect(chute.PhysicsMaterialOverride is { } mat && Mathf.IsEqualApprox(mat.Friction, 0.80f),
               $"Chute Surface Friction reaches the physics material (got {chute.PhysicsMaterialOverride?.Friction})");
    }

    /// <summary>Read fresh on every emission, so the observable is the value the
    /// emitter will use next -- watching a metal carton appear would take
    /// seconds of simulation to prove the same thing.</summary>
    private void CheckEmitter(Emitter emitter)
    {
        Inspect(emitter, "emitter", "Emitter");
        Drive("Metal every Nth", 2);
        Expect(emitter.MetalEvery == 2, $"Metal every Nth reaches the emitter (got {emitter.MetalEvery})");
    }

    /// <summary>The dropdown must offer only tags that exist, and picking one
    /// must move the count onto it -- SceneEditor publishes through TrySet,
    /// which ignores an id nothing owns.</summary>
    private void CheckRemover(Remover remover)
    {
        Inspect(remover, "tall_count", "Remover");

        var picker = FindSettingControl<OptionButton>("Counts into");
        if (picker is null) { Expect(false, "Counts into: no dropdown in the panel"); return; }

        bool allExist = true;
        for (int i = 0; i < picker.ItemCount; i++)
        {
            string id = picker.GetItemText(i).Replace(" (own)", "");
            if (!Tags.Contains(id)) allExist = false;
        }
        Expect(allExist, "every tag the Counts into dropdown offers exists on the bus");

        int shortIndex = -1;
        for (int i = 0; i < picker.ItemCount; i++)
            if (picker.GetItemText(i) == "short_count.count") shortIndex = i;
        if (shortIndex < 0) { Expect(false, "Counts into: short_count.count was not offered"); return; }

        picker.EmitSignal(OptionButton.SignalName.ItemSelected, shortIndex);
        Expect(remover.CountTag == "short_count.count",
               $"picking a tag reaches the remover (got '{remover.CountTag}')");
    }

    private void StartPusher(PusherMechanism pusher)
    {
        Inspect(pusher, "diverter", "PusherMechanism");
        Drive("Stroke Length (m)", 0.90);
        Drive("Stroke Speed (m/s)", 3.00);

        foreach (var tag in Tags)
            if (tag.Id.EndsWith(".extend")) _pusherExtendTag = tag.Id;
        if (_pusherExtendTag.Length > 0) Tags.Force(_pusherExtendTag, true);
    }

    /// <summary>Stroke length and speed are both read every physics tick, so the
    /// observable is the head actually travelling the new distance.</summary>
    private void CheckPusherArrived()
    {
        if (_pusher is null) return;
        Expect(_pusher.IsExtended,
               "the pusher reaches its new 0.90 m stroke within 42 ticks at the new speed");
        if (_pusherExtendTag.Length > 0) Tags.ClearForce(_pusherExtendTag);
    }

    // ---------- the other three scenes

    /// <summary>LE-05: the weighing deck used to be split out of the belt branch
    /// and silently lost the friction control every other belt has.</summary>
    private void CheckRollerScene()
    {
        var weigher = Find<WeighingConveyor>();
        var roller = Find<RollerConveyor>();
        if (weigher is null || roller is null)
        {
            Expect(false, "roller-line-weighing: no weighing or roller conveyor found");
            return;
        }

        Inspect(weigher, "scale", "WeighingConveyor");
        Expect(FindSettingControl<SpinBox>("Belt Speed (m/s)") is not null,
               "a weighing conveyor offers Belt Speed");
        Expect(FindSettingControl<SpinBox>("Surface Friction") is not null,
               "a weighing conveyor offers Surface Friction too (LE-05)");

        CheckWeigherSignal(weigher);

        Inspect(roller, "infeed", "RollerConveyor");
        Drive("Surface Friction", 0.90);
        Expect(roller.PhysicsMaterialOverride is { } mat && Mathf.IsEqualApprox(mat.Friction, 0.90f),
               "a roller deck's friction reaches its physics material");
    }

    private void CheckSensorScene()
    {
        var sensor = Find<PhotoelectricSensor>();
        if (sensor is null) { Expect(false, "start-stop-station: no photoelectric sensor found"); return; }

        Inspect(sensor, "part_present", "PhotoelectricSensor");

        float reachBefore = RayReach(sensor);
        Drive("Beam Range (m)", 1.50);
        Expect(Mathf.IsEqualApprox(RayReach(sensor), 1.50f),
               $"Beam Range rebuilds the raycast: reach {reachBefore:0.###} -> {RayReach(sensor):0.###}");

        float beamBefore = RayHeight(sensor);
        Drive("Beam Height (m)", 0.40);
        Expect(RayHeight(sensor) > beamBefore + 0.05f,
               $"Beam Height rebuilds the raycast: y {beamBefore:0.###} -> {RayHeight(sensor):0.###}");
    }

    private void CheckTankScene()
    {
        var tank = Find<LevelTank>();
        if (tank is null) { Expect(false, "tank-level-control: no level tank found"); return; }

        Inspect(tank, "tank", "LevelTank");
        Drive("Fill Rate (%/s)", 40.0);
        Expect(Mathf.IsEqualApprox(tank.FillRate, 40.0f), $"Fill Rate reaches the tank (got {tank.FillRate})");
        Drive("Drain Rate (%/s)", 35.0);
        Expect(Mathf.IsEqualApprox(tank.DrainRate, 35.0f), $"Drain Rate reaches the tank (got {tank.DrainRate})");

        CheckTankSignal(tank);
    }

    /// <summary>
    /// IP-16. The observable for Signal is what the PLC is handed: the level
    /// tag's type on the bus. The observable for the range is the count the
    /// tag carries -- not the property, which is assigned by construction.
    /// </summary>
    private void CheckTankSignal(LevelTank tank)
    {
        DriveChoice("Signal", "S7 raw 0-27648");
        Expect(Tags.Get("tank.level")?.Type == TagType.Int,
               $"Signal=S7 raw turns tank.level into an INT on the bus (got {Tags.Get("tank.level")?.Type})");

        // The rebuilt panel now offers the range, which it did not while the
        // channel published engineering units.
        Inspect(tank, "tank", "LevelTank");
        Drive("Range Min", -50.0);
        Drive("Range Max", 150.0);
        Editor._PhysicsProcess(1.0 / 60.0);
        int expected = (int)System.Math.Round(27648.0 * (tank.Level + 50.0) / 200.0,
                                              System.MidpointRounding.AwayFromZero);
        int got = System.Convert.ToInt32(Tags.Visible("tank.level"));
        Expect(System.Math.Abs(got - expected) <= 1,
               $"Range Min/Max reach the count the PLC reads: {tank.Level:0.##} % on a -50..150 span "
               + $"reads {got}, expected {expected}");

        DriveChoice("Signal", "Engineering");
        Expect(Tags.Get("tank.level")?.Type == TagType.Float,
               $"Signal=Engineering puts tank.level back to a float (got {Tags.Get("tank.level")?.Type})");
    }

    /// <summary>The load cell's Signal: at 4-20 mA the bus gains a wire-break
    /// contact for it, and loses it again on the way back.</summary>
    private void CheckWeigherSignal(WeighingConveyor weigher)
    {
        DriveChoice("Signal", "4-20 mA raw");
        Expect(Tags.Contains("scale.wirebreak"),
               "Signal=4-20 mA gives the load cell a wire-break contact on the bus");
        Expect(Tags.Get("scale.weight")?.Name.Contains("4-20 mA") == true,
               $"and names its weight tag for what it carries (got '{Tags.Get("scale.weight")?.Name}')");

        // Same observable as the tank's: the count on the bus.
        Inspect(weigher, "scale", "WeighingConveyor");
        Drive("Range Min", -1000.0);
        Drive("Range Max", 1000.0);
        Editor._PhysicsProcess(1.0 / 60.0);
        int expected = (int)System.Math.Round(27648.0 * (weigher.MeasuredWeight + 1000.0) / 2000.0,
                                              System.MidpointRounding.AwayFromZero);
        int got = System.Convert.ToInt32(Tags.Visible("scale.weight"));
        Expect(System.Math.Abs(got - expected) <= 1,
               $"the load cell's range reaches its count: {weigher.MeasuredWeight:0} g on a -1000..1000 g "
               + $"span reads {got}, expected {expected}");

        DriveChoice("Signal", "Engineering");
        Expect(!Tags.Contains("scale.wirebreak"),
               "Signal=Engineering takes the wire-break contact away again");
    }

    /// <summary>The setpoint pot's scale plate. A panel dragged in from the
    /// palette used to get the hardcoded 0-100 "%" default with no way to
    /// change it, and nothing here noticed, because this test had never
    /// inspected a ButtonPanel at all (OP-01).
    ///
    /// The observable is the plate itself -- what the instrument reads -- not
    /// the property that was assigned. A range that reached the field and not
    /// the plate is exactly the class of failure this whole test exists for.
    /// </summary>
    private void CheckPanelScene()
    {
        var panel = Find<ButtonPanel>();
        if (panel is null) { Expect(false, "tank-level-control: no ButtonPanel found"); return; }

        Inspect(panel, "panel", "ButtonPanel");

        Drive("Scale Max", 250.0);
        Expect(Mathf.IsEqualApprox(panel.SetpointMax, 250.0f),
               $"Scale Max reaches the panel (got {panel.SetpointMax})");

        DriveText("Scale Unit", "bar");
        Expect(panel.SetpointUnit == "bar", $"Scale Unit reaches the panel (got '{panel.SetpointUnit}')");
        Expect(panel.PlateText.EndsWith("bar"),
               $"the unit reaches the scale plate, not just the property (plate reads '{panel.PlateText}')");

        Drive("Setpoint", 120.0);
        // Within the control's own 0.01 step, not to the last float bit: the
        // SpinBox quantises to its step across a ±10000 range, so it lands on
        // 119.9998. Demanding better would be demanding the widget be a
        // different widget.
        Expect(Mathf.Abs(panel.Setpoint - 120.0f) <= 0.01f,
               $"Setpoint reaches the pot (got {panel.Setpoint})");
        Expect(panel.PlateText.Contains("120"),
               $"the pot's value reaches the plate (plate reads '{panel.PlateText}')");

        // A setpoint outside the plate is not a setpoint, it is a mislabelled
        // instrument -- so the range has to clamp it, and the plate has to show
        // the clamped value rather than the one that was asked for.
        Drive("Setpoint", 9000.0);
        Expect(Mathf.IsEqualApprox(panel.Setpoint, 250.0f),
               $"the range clamps a setpoint typed past the top of the plate (got {panel.Setpoint})");

        Drive("Scale Min", 300.0);
        Expect(Mathf.IsEqualApprox(panel.Setpoint, 300.0f),
               $"raising the bottom of the plate carries the pot up with it (got {panel.Setpoint})");
    }

    // ---------- IP-17: the five industrial parts, in a scene of their own

    private void LoadIndustrialScene()
    {
        var data = new SceneData { Name = "selftest-partsettings-industrial" };
        void Add(string id, string type, float z) => data.Parts.Add(new PartInstanceData
        {
            Id = id,
            Type = type,
            Position = new[] { 0.0f, PartLayout.WorkPlaneY, z },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
        });
        Add("ls", "LimitSwitch", 0.0f);
        Add("valve", "SolenoidValve", 2.0f);
        Add("sd", "StarDeltaStarter", 4.0f);
        Add("air", "PressureTransmitter", 6.0f);
        Add("axis", "ServoAxis", 8.0f);

        const string path = "user://selftest_partsettings_industrial.json";
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Editor.LoadTemplate(path);
    }

    /// <summary>
    /// Each row's observable is what the part then <em>does</em>, measured by
    /// driving the part's own step directly -- a lever that reaches further, a
    /// contact that chatters, a valve that arrives sooner, a motor that draws
    /// more, a receiver that settles lower, an axis that stops accelerating
    /// sooner -- never that a property holds the number just typed.
    /// </summary>
    private void CheckIndustrialScene()
    {
        // This test predates gotcha 12's try/catch; the new phase has one, so
        // a throw here is a failure rather than a run that times out green.
        try
        {
            CheckLimitSwitchSettings();
            CheckValveSettings();
            CheckStarDeltaSettings();
            CheckTransmitterSettings();
            CheckServoSettings();
        }
        catch (System.Exception ex)
        {
            Expect(false, $"industrial settings threw: {ex.GetType().Name}: {ex.Message}");
        }
    }

    private static RayCast3D? Probe(Node part)
    {
        foreach (var child in part.GetChildren())
            if (child is RayCast3D ray) return ray;
        return null;
    }

    private void CheckLimitSwitchSettings()
    {
        var sw = Find<LimitSwitch>();
        if (sw is null) { Expect(false, "industrial scene: no limit switch"); return; }
        Inspect(sw, "ls", "LimitSwitch");

        Drive("Lever (m)", 0.12);
        float reach = Probe(sw) is { } ray ? Mathf.Abs(ray.TargetPosition.Z) : 0.0f;
        Expect(Mathf.IsEqualApprox(reach, 0.14f),
               $"Lever rebuilds the switch: the lever reaches {reach:0.000} m (0.12 lever + 0.02 roller)");

        float before = Probe(sw)?.Position.Y ?? 0.0f;
        Drive("Height (m)", 0.20);
        float after = Probe(sw)?.Position.Y ?? 0.0f;
        Expect(Mathf.IsEqualApprox(after - before, 0.15f),
               $"Height rebuilds the switch: the lever rose {after - before:0.000} m (0.05 -> 0.20)");

        Drive("Bounce (ms)", 60.0);
        int rises = 0;
        bool last = sw.NoClosed;
        for (int i = 0; i < 8; i++)
        {
            sw.StepContacts(30.0f, 1.0f / 60.0f);
            if (sw.NoClosed && !last) rises++;
            last = sw.NoClosed;
        }
        sw.StepContacts(0.0f, 1.0f / 60.0f);
        Expect(rises >= 2, $"Bounce makes the NO contact chatter on make ({rises} rising edges for one actuation)");
    }

    private void CheckValveSettings()
    {
        var valve = Find<SolenoidValve>();
        if (valve is null) { Expect(false, "industrial scene: no solenoid valve"); return; }
        Inspect(valve, "valve", "SolenoidValve");

        Drive("Travel Time (s)", 0.5);
        valve.Step(true, 0.25f);
        Expect(Mathf.Abs(valve.Opening - 0.5f) < 0.01f,
               $"Travel Time reaches the actuator: a quarter second of a 0.5 s stroke is half open ({valve.Opening:0.00})");
        valve.Step(false, 1.0f);
    }

    /// <summary>Star inrush with the motor at standstill, and speed after a
    /// fixed time in star, from the starter's own model.</summary>
    private static (float inrush, float speedAfter) StarRun(StarDeltaStarter sd, float seconds)
    {
        sd.ResetPart(new PartReset(null, "sd"));
        sd.Step(true, true, false, 0.05f);
        float inrush = 0.0f;
        for (float t = 0.05f; t < seconds; t += 0.05f)
        {
            sd.Step(true, true, false, 0.05f);
            if (inrush == 0.0f && sd.Current > 0.0f) inrush = sd.Current;
        }
        float speed = sd.Speed;
        sd.ResetPart(new PartReset(null, "sd"));
        return (inrush, speed);
    }

    private void CheckStarDeltaSettings()
    {
        var sd = Find<StarDeltaStarter>();
        if (sd is null) { Expect(false, "industrial scene: no star-delta starter"); return; }
        Inspect(sd, "sd", "StarDeltaStarter");

        var (inrush20, speed60) = StarRun(sd, 8.0f);
        Drive("Full Load (A)", 40.0);
        var (inrush40, _) = StarRun(sd, 8.0f);
        Expect(Mathf.Abs(inrush40 / Mathf.Max(inrush20, 0.001f) - 2.0f) < 0.05f,
               $"Full Load scales the current the motor draws: star inrush {inrush20:0.0} A -> {inrush40:0.0} A");

        Drive("Motor Load (%)", 150.0);
        var (_, speed150) = StarRun(sd, 8.0f);
        Expect(speed150 < speed60 - 0.1f,
               $"Motor Load changes where the motor settles in star: {speed60 * 100:0} % at 60 % load, {speed150 * 100:0} % at 150 %");
        Drive("Motor Load (%)", 60.0);

        var (_, quick) = StarRun(sd, 1.0f);
        Drive("Inertia (s)", 8.0);
        var (_, slow) = StarRun(sd, 1.0f);
        Expect(slow < quick * 0.5f,
               $"Inertia slows the run-up: {quick * 100:0} % after 1 s at 2 s inertia, {slow * 100:0} % at 8 s");

        DriveChoice("Signal", "S7 raw 0-27648");
        Expect(Tags.Get("sd.current")?.Type == TagType.Int,
               $"Signal=S7 raw turns sd.current into an INT on the bus (got {Tags.Get("sd.current")?.Type})");
        Inspect(sd, "sd", "StarDeltaStarter");
        Drive("Range Min", -100.0);
        Drive("Range Max", 100.0);
        Editor._PhysicsProcess(1.0 / 60.0);
        int expected = (int)System.Math.Round(27648.0 * (sd.Current + 100.0) / 200.0, System.MidpointRounding.AwayFromZero);
        int got = System.Convert.ToInt32(Tags.Visible("sd.current"));
        Expect(System.Math.Abs(got - expected) <= 1,
               $"Range Min/Max reach the count: {sd.Current:0.0} A on a -100..100 A span reads {got}, expected {expected}");
        DriveChoice("Signal", "Engineering");
        Expect(Tags.Get("sd.current")?.Type == TagType.Float, "Signal=Engineering puts sd.current back to a float");
    }

    private static float Settle(PressureTransmitter air)
    {
        air.ResetPart(new PartReset(null, "air"));
        for (int i = 0; i < 600; i++) air.Step(true, 0.1f);
        float settled = air.Pressure;
        air.ResetPart(new PartReset(null, "air"));
        return settled;
    }

    private void CheckTransmitterSettings()
    {
        var air = Find<PressureTransmitter>();
        if (air is null) { Expect(false, "industrial scene: no pressure transmitter"); return; }
        Inspect(air, "air", "PressureTransmitter");

        float at8 = Settle(air);
        Drive("Supply (bar)", 4.0);
        float at4 = Settle(air);
        Expect(Mathf.Abs(at4 - at8 / 2.0f) < 0.05f,
               $"Supply sets what the receiver fills towards: {at8:0.00} bar from an 8 bar main, {at4:0.00} from 4");

        Drive("Consumption (%)", 75.0);
        float heavy = Settle(air);
        Expect(heavy < at4 - 0.5f, $"Consumption pulls the balance point down: {at4:0.00} -> {heavy:0.00} bar");

        var signal = FindSettingControl<OptionButton>("Signal");
        bool offersEngineering = false;
        if (signal is not null)
            for (int i = 0; i < signal.ItemCount; i++)
                if (signal.GetItemText(i) == "Engineering") offersEngineering = true;
        Expect(signal is not null && signal.ItemCount == 2 && !offersEngineering,
               $"the transmitter's Signal offers the two raw types and never engineering units ({signal?.ItemCount} items)");
        Expect(Tags.Contains("air.wirebreak"), "4-20 mA by default, with its wire break on the bus");

        DriveChoice("Signal", "S7 raw 0-27648");
        Expect(!Tags.Contains("air.wirebreak") && Tags.Get("air.pressure")?.Type == TagType.Int,
               "Signal=S7 raw keeps the INT and drops the wire break a 0-20 mA channel cannot detect");

        Inspect(air, "air", "PressureTransmitter");
        Drive("Range Min", -1.0);
        Drive("Range Max", 15.0);
        Editor._PhysicsProcess(1.0 / 60.0);
        int expected = (int)System.Math.Round(27648.0 * (air.Pressure + 1.0) / 16.0, System.MidpointRounding.AwayFromZero);
        int got = System.Convert.ToInt32(Tags.Visible("air.pressure"));
        Expect(System.Math.Abs(got - expected) <= 1,
               $"Range Min/Max reach the count: {air.Pressure:0.00} bar on a -1..15 bar span reads {got}, expected {expected}");
    }

    private void CheckServoSettings()
    {
        var axis = Find<ServoAxis>();
        if (axis is null) { Expect(false, "industrial scene: no servo axis"); return; }
        Inspect(axis, "axis", "ServoAxis");

        Drive("Stroke (mm)", 2000.0);
        float rail = 0.0f;
        foreach (var child in axis.GetChildren())
            if (child is MeshInstance3D { Mesh: BoxMesh box } mesh && mesh.Name == "Profile") rail = box.Size.X;
        Expect(Mathf.IsEqualApprox(rail, 2.2f), $"Stroke rebuilds the rail: {rail:0.00} m for 2000 mm of travel");

        const float dt = 0.01f;
        void Enable()
        {
            axis.ResetPart(new PartReset(null, "axis"));
            for (int i = 0; i < 20; i++) axis.Step(true, false, false, 0.0f, 100.0f, dt);
        }

        Drive("Max Speed (mm/s)", 100.0);
        Enable();
        for (int i = 0; i < 100; i++) axis.Step(true, false, false, 1500.0f, 1000.0f, dt);
        float p1 = axis.ActualPosition;
        for (int i = 0; i < 50; i++) axis.Step(true, false, false, 1500.0f, 1000.0f, dt);
        float speed = (axis.ActualPosition - p1) / 0.5f;
        Expect(Mathf.Abs(speed - 100.0f) < 2.0f, $"Max Speed limits the axis: {speed:0.0} mm/s asked 1000");

        Drive("Max Speed (mm/s)", 3000.0);
        Drive("Accel (mm/s2)", 500.0);
        Enable();
        for (int i = 0; i < 20; i++) axis.Step(true, false, false, 1500.0f, 3000.0f, dt);
        // Half of 500 mm/s^2 over 0.2 s is 10 mm; at the default 2000 it would be 40.
        Expect(axis.ActualPosition > 8.0f && axis.ActualPosition < 12.0f,
               $"Accel sets the acceleration: {axis.ActualPosition:0.0} mm after 0.2 s at 500 mm/s2");

        Drive("Window (mm)", 5.0);
        Enable();
        for (int i = 0; i < 5; i++) axis.Step(true, false, false, 3.0f, 0.0f, dt);
        Expect(axis.InPosition && !axis.HasError,
               "Window decides what counts as there: a target 3 mm off is in position inside a 5 mm window");
        axis.ResetPart(new PartReset(null, "axis"));
    }

    // ---------- panel driving

    /// <summary>Select a part and check the rows it produced are all ones this
    /// test knows how to drive.</summary>
    private void Inspect(Node3D node, string instanceId, string partType)
    {
        _panel.InspectNode(node, instanceId, partType);

        // No row may be wider than the panel's own scroll bound. The Remover's
        // tag dropdown broke this the day it was added -- an OptionButton takes
        // the width of its longest menu item, so one long tag id pushed the
        // whole panel off the right of the screen (LE-04). Nothing else would
        // have caught it: the panel's *minimum* size stayed 280 throughout.
        float widest = WidestRow();
        Expect(widest <= PanelContentWidth,
               $"{partType}: widest row is {widest}px, over the panel's {PanelContentWidth}px content width");
        var expected = new HashSet<string>(Covered.TryGetValue(partType, out var rows) ? rows : new string[0]);
        foreach (string label in SettingRowLabels())
        {
            Expect(expected.Contains(label),
                   $"{partType}: settings row '{label}' has no check in PartSettingsSelfTest");
        }
    }

    /// <summary>Type into a text setting the way a keyboard does. LineEdit's
    /// TextChanged does not fire for a programmatic assignment, so the signal
    /// is emitted explicitly -- the alternative is a test that sets a field
    /// nothing is listening to and calls that a pass.</summary>
    private void DriveText(string label, string value)
    {
        var field = FindSettingControl<LineEdit>(label);
        if (field is null) { Expect(false, $"no settings control labelled '{label}'"); return; }
        field.Text = value;
        field.EmitSignal(LineEdit.SignalName.TextChanged, value);
    }

    /// <summary>Pick a dropdown entry the way a click does, by its text.</summary>
    private void DriveChoice(string label, string item)
    {
        var picker = FindSettingControl<OptionButton>(label);
        if (picker is null) { Expect(false, $"no dropdown labelled '{label}'"); return; }
        for (int i = 0; i < picker.ItemCount; i++)
        {
            if (picker.GetItemText(i) != item) continue;
            picker.Select(i);
            picker.EmitSignal(OptionButton.SignalName.ItemSelected, i);
            return;
        }
        Expect(false, $"'{label}' offers no '{item}'");
    }

    /// <summary>Move a settings control the way a drag does, by its label.</summary>
    private void Drive(string label, double value)
    {
        var spin = FindSettingControl<SpinBox>(label);
        if (spin is null) { Expect(false, $"no settings control labelled '{label}'"); return; }
        spin.Value = value;
    }

    private float WidestRow()
    {
        float widest = 0.0f;
        foreach (var row in Rows(_panel)) widest = Mathf.Max(widest, row.GetCombinedMinimumSize().X);
        return widest;
    }

    private IEnumerable<string> SettingRowLabels()
    {
        foreach (var row in Rows(_panel))
        {
            // The I/O rows below carry the tag id as a tooltip; the Name row is
            // the only other labelled row, and it is not a setting.
            if (row.GetChildCount() == 0 || row.GetChild(0) is not Label label) continue;
            if (label.TooltipText.Length > 0 || label.Text == "Name") continue;
            yield return label.Text;
        }
    }

    private T? FindSettingControl<T>(string labelText) where T : Control
    {
        foreach (var row in Rows(_panel))
        {
            if (row.GetChildCount() == 0 || row.GetChild(0) is not Label label) continue;
            if (label.Text != labelText) continue;
            foreach (var child in row.GetChildren())
                if (child is T match) return match;
        }
        return null;
    }

    private static IEnumerable<HBoxContainer> Rows(Node node)
    {
        foreach (var child in node.GetChildren())
        {
            if (child is HBoxContainer row) yield return row;
            foreach (var nested in Rows(child)) yield return nested;
        }
    }

    // ---------- observables

    private static float TopBeamY(LightArray curtain)
    {
        float top = 0.0f;
        foreach (var child in curtain.GetChildren())
            if (child is RayCast3D ray) top = Mathf.Max(top, ray.Position.Y);
        return top;
    }

    private static int BeamCount(LightArray curtain)
    {
        int n = 0;
        foreach (var child in curtain.GetChildren())
            if (child is RayCast3D) n++;
        return n;
    }

    private static float RayReach(Node sensor)
    {
        foreach (var child in sensor.GetChildren())
            if (child is RayCast3D ray) return Mathf.Abs(ray.TargetPosition.Z);
        return 0.0f;
    }

    private static float RayHeight(Node sensor)
    {
        foreach (var child in sensor.GetChildren())
            if (child is RayCast3D ray) return ray.Position.Y;
        return 0.0f;
    }

    private static Transform3D FirstShapeTransform(Node part)
    {
        foreach (var child in part.GetChildren())
            if (child is CollisionShape3D shape) return shape.Transform;
        return Transform3D.Identity;
    }

    private T? Find<T>() where T : Node
    {
        return Search<T>(GetParent());
    }

    private static T? Search<T>(Node node) where T : Node
    {
        foreach (var child in node.GetChildren())
        {
            if (child is T match) return match;
            if (Search<T>(child) is { } found) return found;
        }
        return null;
    }

    private void Finish()
    {
        if (_failures.Count == 0)
        {
            GD.Print("self-test partsettings: PASS");
            GetTree().Quit(0);
            return;
        }
        GD.PrintErr($"self-test partsettings: FAIL ({_failures.Count})");
        foreach (var failure in _failures) GD.PrintErr($"  - {failure}");
        GetTree().Quit(1);
    }
}
