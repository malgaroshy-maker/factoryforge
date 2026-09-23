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
            case 57: CheckTankScene(); CheckPanelScene(); Finish(); return;
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
