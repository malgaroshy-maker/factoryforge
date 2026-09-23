using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Raw analog, the way a PLC actually sees it (IP-16):
///
/// <code>godot --headless --path engine -- --self-test=analog</code>
///
/// Five analog inputs -- the tank's level, the heater's temperature, the flow
/// meter's rate, the starter's motor current and the weighing conveyor's load
/// -- each driven to 0 %, 50 % and 100 % of its measuring range in each of the
/// three signal modes, with the published value checked against this file's
/// own arithmetic. The constants are written out here (27648, 32767, -32768...)
/// rather than borrowed from <see cref="AnalogSignal"/>, so a wrong constant in
/// the helper is a failure here and not an agreement with itself.
///
/// Asserts effects, not existence (gotcha 16). The mode is changed through the
/// property panel's own dropdown, which is the path a person takes, and each
/// change has to re-declare the tag with its new type and announce it. A wire
/// break has to read 32767 at 4-20 mA and has to clear again. The settings
/// have to survive a save and a load, and the reloaded range has to change the
/// counts a program sees -- not merely reappear in a dictionary.
///
/// Four of the parts are stepped by hand (a thermal soak costs no wall time);
/// the load cell needs real cartons on a real deck, so its checks run on real
/// physics frames between the hand-turned bursts.
/// </summary>
public partial class AnalogSignalSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    // --- the S7-1500 analog value representation, written out independently
    private const int FullScale = 27648;
    private const int OverrangeTop = 32511;
    private const int OverflowCode = 32767;
    private const int UnderrangeBottom = -4864;
    private const int UnderflowCode = -32768;

    /// <summary>
    /// How far a published count may sit from this file's own arithmetic.
    ///
    /// One count, 0.0036 % of span. The part computes in single precision and
    /// this file in double, so a reading that lands within a hair of a rounding
    /// boundary can round either way; any error in the scaling itself -- the
    /// wrong full scale, the wrong offset, a truncation where there should be
    /// a rounding across most of the range -- is tens to thousands of counts.
    /// </summary>
    private const int CountTolerance = 1;

    private const double Tick = 1.0 / 60.0;

    private readonly List<string> _failures = new();
    private int _step;
    private bool _done;
    private int _tagsChanged;
    private PartPropertyInspectorUI _panel = null!;
    private TagBusServer? _bus;

    /// <summary>One carton's weight in grams, measured in engineering mode and
    /// then used to span the load cell so one carton is 50 % and two are
    /// 100 %.</summary>
    private float _cartonGrams;

    private const string ScenePath = "user://selftest_analog.json";
    private const string RoundTripPath = "user://selftest_analog_roundtrip.json";

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    // ---------------------------------------------------------------- phases

    /// <summary>Physics frames at which each phase runs. The gaps are for the
    /// load cell: a carton dropped 14 cm onto a stopped deck has settled well
    /// inside forty frames.</summary>
    public override void _PhysicsProcess(double delta)
    {
        if (_done) return;
        _step++;

        try
        {
            switch (_step)
            {
                case 2: BuildScene(); return;
                case 4: Setup(); EngineeringPhase(); return;

                // Load cell, engineering: grams as ever, and one carton's
                // weight becomes half the span for the raw phases. Each
                // phase's ProcessChecks resets the scene, which clears the
                // deck; a freed carton leaves the scale area at the end of
                // that frame, so the deck is read a few frames later.
                case 8: ScaleEmptyThenLoad(AnalogSignalMode.Engineering); return;
                case 48: CalibrateScale(); return;

                case 50: SwitchAll(AnalogSignalMode.S7Raw); ProcessChecks(AnalogSignalMode.S7Raw);
                    S7ConventionChecks(); return;
                case 54: ScaleEmptyThenLoad(AnalogSignalMode.S7Raw); return;
                case 94: ScaleOneThenTwo(); return;
                case 134: ScaleFull(); return;

                case 136: SwitchAll(AnalogSignalMode.Ma4To20); ProcessChecks(AnalogSignalMode.Ma4To20);
                    WireBreakChecks(); return;
                case 140: ScaleEmptyThenLoad(AnalogSignalMode.Ma4To20); return;
                case 180: ScaleOneThenTwo(); return;
                case 220: ScaleFull(); ScaleWireBreak(); return;

                case 222: SaveAndReload(); return;
                case 226: CheckReloaded(); break;
                default: return;
            }
        }
        catch (Exception ex)
        {
            // Gotcha 12: a throw inside _PhysicsProcess is logged and the run
            // carries on to --duration, exiting 0. Count it.
            _failures.Add($"threw: {ex.GetType().Name}: {ex.Message}");
            GD.PrintErr($"  FAIL  threw at step {_step}: {ex}");
        }

        Finish();
    }

    // ---------------------------------------------------------------- scene

    private static PartInstanceData At(string id, string type, float x, float z,
                                       Dictionary<string, string>? props = null) => new()
    {
        Id = id,
        Type = type,
        Position = new[] { x, PartLayout.WorkPlaneY, z },
        Rotation = new[] { 0.0f, 0.0f, 0.0f },
        Properties = props,
    };

    private void BuildScene()
    {
        var data = new SceneData { Name = "selftest-analog" };

        // Everything well apart: a pump pumps into any tank in reach, and a
        // meter measures any pump in reach, so the only pump sits beside the
        // only meter and four metres from the tank.
        data.Parts.Add(At("tank", "LevelTank", 0.0f, 0.0f));
        data.Parts.Add(At("oven", "HeatingStation", 0.0f, -4.0f, new()
        {
            // Ambient 0 degC, so a reset lands on exactly 0 % of a 0..300 degC
            // span; a light plate, so it settles in seconds of sim time; and
            // 90 / 0.30 = 300 degC at full power, so 50 % and 100 % power
            // settle at exactly 50 % and 100 % of span.
            ["ambient"] = "0", ["thermal_mass"] = "1",
            ["heater_power"] = "90", ["loss_rate"] = "0.3",
            ["range_min"] = "0", ["range_max"] = "300",
        }));
        data.Parts.Add(At("pump", "DosingPump", 0.0f, -8.0f, new() { ["rated_flow"] = "40" }));
        data.Parts.Add(At("flow", "FlowMeter", 0.5f, -8.0f, new()
        {
            ["range_min"] = "0", ["range_max"] = "40",
        }));
        data.Parts.Add(At("starter", "MotorStarter", 0.0f, -12.0f, new()
        {
            // A 6 A motor on a 0..12 A transducer: rated load is half scale,
            // twice rated is full scale, and the 36 A inrush is overflow.
            ["fla"] = "6", ["load_percent"] = "100", ["range_min"] = "0", ["range_max"] = "12",
        }));
        data.Parts.Add(At("scale", "WeighingConveyor", 0.0f, 4.0f, new()
        {
            ["size_x"] = "2", ["size_y"] = "0.12", ["size_z"] = "0.5",
        }));

        using (var file = Godot.FileAccess.Open(ScenePath, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Expect(Editor.LoadSceneFromFile(ScenePath), "the analog test scene loads");
    }

    private void Setup()
    {
        _panel = Editor.PropertyInspector!;
        if (_panel is null)
        {
            // Headless has no panel of its own. Built with the real editor
            // behind it, so a choice made here goes through the same
            // PartSettingsChanged a click in the window does.
            _panel = new PartPropertyInspectorUI { Name = "AnalogTestInspector", Editor = Editor };
            AddChild(_panel);
        }
        Editor.TagsChanged += () => _tagsChanged++;
        _bus = GetTree().Root.FindChild("TagBus", true, false) as TagBusServer;
    }

    // ---------------------------------------------------------------- helpers

    private void Run(int ticks)
    {
        for (int i = 0; i < ticks; i++) Editor._PhysicsProcess(Tick);
    }

    private T Part<T>(string id) where T : Node3D =>
        Editor.NodeFor(id) as T ?? throw new InvalidOperationException($"'{id}' is not a {typeof(T).Name}");

    private LevelTank TankPart => Part<LevelTank>("tank");
    private HeatingStation Oven => Part<HeatingStation>("oven");
    private FlowMeter Meter => Part<FlowMeter>("flow");
    private MotorStarter Starter => Part<MotorStarter>("starter");
    private WeighingConveyor Scale => Part<WeighingConveyor>("scale");

    /// <summary>The five channels under test: part id, measurement tag,
    /// part type, and the engineering type the tag has always had.</summary>
    private static readonly (string Id, string Tag, string Type, TagType Engineering)[] Channels =
    {
        ("tank", "tank.level", "LevelTank", TagType.Float),
        ("oven", "oven.temperature", "HeatingStation", TagType.Float),
        ("flow", "flow.rate", "FlowMeter", TagType.Float),
        ("starter", "starter.current", "MotorStarter", TagType.Float),
        ("scale", "scale.weight", "WeighingConveyor", TagType.Int),
    };

    private static int ExpectedRaw(double engineering, double min, double max)
    {
        double counts = Math.Round(FullScale * (engineering - min) / (max - min),
                                   MidpointRounding.AwayFromZero);
        if (counts > OverrangeTop) return OverflowCode;
        if (counts < UnderrangeBottom) return UnderflowCode;
        return (int)counts;
    }

    private int RawOf(string tagId) =>
        Tags.Get(tagId) is { Type: TagType.Int } ? Convert.ToInt32(Tags.Visible(tagId)) : int.MinValue;

    /// <summary>A published count, within <see cref="CountTolerance"/> of
    /// <paramref name="expected"/>.</summary>
    private void ExpectCount(string tagId, int expected, string what, int tolerance = CountTolerance)
    {
        var tag = Tags.Get(tagId);
        if (tag is null) { Expect(false, $"{what}: {tagId} does not exist"); return; }
        if (tag.Type != TagType.Int) { Expect(false, $"{what}: {tagId} is {tag.Type}, not an INT"); return; }
        int got = Convert.ToInt32(Tags.Visible(tagId));
        Expect(Math.Abs(got - expected) <= tolerance, $"{what}: {tagId} reads {got}, expected {expected}");
    }

    private static AnalogSignal SignalOf(Node3D part) => part switch
    {
        LevelTank t => t.LevelSignal,
        HeatingStation h => h.TemperatureSignal,
        FlowMeter f => f.RateSignal,
        MotorStarter m => m.CurrentSignal,
        WeighingConveyor w => w.WeightSignal,
        _ => throw new InvalidOperationException($"{part.Name} has no analog signal"),
    };

    /// <summary>The live engineering reading of each channel's part.</summary>
    private double Reading(string id) => id switch
    {
        "tank" => TankPart.Level,
        "oven" => Oven.Temperature,
        "flow" => Meter.Rate,
        "starter" => Starter.Current,
        "scale" => Scale.MeasuredWeight,
        _ => throw new InvalidOperationException(id),
    };

    // ---------------------------------------------------------------- mode changes

    /// <summary>
    /// Change one part's signal the way a person does: select it, open the
    /// Signal dropdown, pick an entry. The panel hands the choice to the part
    /// and asks the editor to bring the tag table into line.
    /// </summary>
    private void Choose(string id, string partType, AnalogSignalMode mode)
    {
        var node = Editor.NodeFor(id);
        if (node is null) { Expect(false, $"no part '{id}' to inspect"); return; }
        _panel.InspectNode(node, id, partType);

        var picker = FindSettingControl<OptionButton>("Signal");
        if (picker is null) { Expect(false, $"{partType}: the property panel offers no Signal dropdown"); return; }
        Expect(picker.ItemCount == 3, $"{partType}: the Signal dropdown offers {picker.ItemCount} modes, not 3");

        int index = -1;
        for (int i = 0; i < picker.ItemCount; i++)
            if (picker.GetItemText(i) == AnalogSignal.LabelFor(mode)) index = i;
        if (index < 0) { Expect(false, $"{partType}: no '{AnalogSignal.LabelFor(mode)}' entry"); return; }

        picker.Select(index);
        picker.EmitSignal(OptionButton.SignalName.ItemSelected, index);
    }

    private void SwitchAll(AnalogSignalMode mode)
    {
        int epochBefore = _bus?.Epoch ?? 0;
        int before = _tagsChanged;
        foreach (var (id, _, type, _) in Channels) Choose(id, type, mode);

        // One announcement per part whose I/O actually changed -- here, all
        // five -- and none for anything else.
        Expect(_tagsChanged - before == Channels.Length,
               $"switching five channels to {mode} announced {_tagsChanged - before} tag-set changes, "
               + $"not {Channels.Length}");

        // The windowed engine answers TagsChanged with a fresh describe; the
        // headless one never wired that up, because nothing headless edits a
        // scene. So the epoch is only asserted where it can move.
        if (_bus is not null && DisplayServer.GetName() != "headless")
            Expect(_bus.Epoch > epochBefore,
                   $"the bus re-described after switching to {mode} (epoch {epochBefore} -> {_bus.Epoch})");

        CheckDeclared(mode);
    }

    /// <summary>The declared type per mode, as the table holds it and as the
    /// describe a driver receives would carry it.</summary>
    private void CheckDeclared(AnalogSignalMode mode)
    {
        JsonArray describe = Tags.ToJson();
        foreach (var (id, tagId, type, engineering) in Channels)
        {
            var tag = Tags.Get(tagId);
            if (tag is null) { Expect(false, $"{mode}: {tagId} vanished"); continue; }

            TagType want = mode == AnalogSignalMode.Engineering ? engineering : TagType.Int;
            Expect(tag.Type == want, $"{mode}: {tagId} is declared {tag.Type}, expected {want}");
            Expect(tag.Kind == TagKind.Input, $"{mode}: {tagId} is an {tag.Kind}; a measurement is an input");
            Expect(SignalOf(Editor.NodeFor(id)!).Mode == mode, $"{mode}: {id}'s channel reports {SignalOf(Editor.NodeFor(id)!).Mode}");

            bool raw = mode != AnalogSignalMode.Engineering;
            Expect(tag.Name.Contains("raw") == raw,
                   $"{mode}: {tagId} is named '{tag.Name}', which does not say what it carries");

            bool wantsBreak = mode == AnalogSignalMode.Ma4To20;
            string wireId = $"{id}.wirebreak";
            var wire = Tags.Get(wireId);
            Expect((wire is not null) == wantsBreak,
                   wantsBreak ? $"{mode}: {wireId} is missing"
                              : $"{mode}: {wireId} exists, but only a 4-20 mA channel can detect a broken wire");
            if (wire is not null)
                Expect(wire.Type == TagType.Bit && wire.Kind == TagKind.Input,
                       $"{wireId} is a {wire.Type} {wire.Kind}, expected a bit input like .fault");

            JsonObject? entry = null;
            foreach (var node in describe)
                if (node is JsonObject o && (string?)o["id"] == tagId) entry = o;
            string wantName = want == TagType.Int ? "int" : "float";
            Expect(entry is not null && (string?)entry["type"] == wantName,
                   $"{mode}: the describe a driver receives says {tagId} is "
                   + $"{(entry is null ? "absent" : (string?)entry["type"])}, not {wantName}");
            if (entry is not null && want == TagType.Int)
                Expect(!entry["value"]!.ToJsonString().Contains('.'),
                       $"{mode}: {tagId}'s describe value {entry["value"]!.ToJsonString()} is not an integer");
        }
    }

    // ---------------------------------------------------------------- phase: engineering

    /// <summary>The default changes nothing: the same types and names every
    /// existing program was written against, and choosing the mode the part is
    /// already in announces nothing at all.</summary>
    private void EngineeringPhase()
    {
        CheckDeclared(AnalogSignalMode.Engineering);
        Expect(Tags.Get("tank.level")?.Name.EndsWith("Level (%)") == true,
               $"engineering keeps the tank's old tag name (got '{Tags.Get("tank.level")?.Name}')");
        Expect(Tags.Get("scale.weight")?.Name.EndsWith("Weight") == true,
               $"engineering keeps the weigher's old tag name (got '{Tags.Get("scale.weight")?.Name}')");

        int before = _tagsChanged;
        Choose("tank", "LevelTank", AnalogSignalMode.Engineering);
        Expect(_tagsChanged == before, "re-choosing the mode a channel is already in announces nothing");

        ProcessChecks(AnalogSignalMode.Engineering);
    }

    // ---------------------------------------------------------------- the four hand-stepped channels

    /// <summary>
    /// Tank, heater, flow meter and starter, each at 0 %, 50 % and 100 % of
    /// span. Every point is checked against the part's own reading; where the
    /// process lands on the point exactly (a reset, a clamp), it is also
    /// checked against the bare number, with nothing of the part's in the
    /// arithmetic at all.
    /// </summary>
    private void ProcessChecks(AnalogSignalMode mode)
    {
        // A re-declared tag powers up at the live reading, not at zero.
        if (mode != AnalogSignalMode.Engineering)
        {
            foreach (var (id, tagId, _, _) in Channels)
            {
                var signal = SignalOf(Editor.NodeFor(id)!);
                ExpectCount(tagId, ExpectedRaw(Reading(id), signal.RangeMin, signal.RangeMax),
                            $"{mode}: straight after the switch, before a tick");
            }
        }

        Editor.ResetItems();
        Run(1);
        CheckPoint(mode, "tank", "tank.level", 0.0, "reset, empty");
        CheckPoint(mode, "oven", "oven.temperature", 0.0, "reset, at 0 degC ambient");
        CheckPoint(mode, "flow", "flow.rate", 0.0, "reset, no pump running");
        CheckPoint(mode, "starter", "starter.current", 0.0, "reset, contactor out");
        if (mode != AnalogSignalMode.Engineering)
        {
            foreach (var (_, tagId, _, _) in Channels)
                if (tagId != "scale.weight") ExpectCount(tagId, 0, $"{mode}: 0 % of span after a reset reads 0");
        }

        // --- 50 %
        // Tank: fill until the level crosses half, then shut the valve. It
        // stops within one tick's worth of inflow of 50 %.
        Tags.Set("tank.drain", 0.0);
        Tags.Set("tank.fill", 100.0);
        for (int i = 0; i < 2000 && TankPart.Level < 50.0f; i++) Run(1);
        Tags.Set("tank.fill", 0.0);
        Run(1);
        Expect(TankPart.Level >= 50.0f && TankPart.Level < 50.5f,
               $"{mode}: the tank was driven to half full (level {TankPart.Level:0.###} %)");

        Tags.Set("oven.heater", 50.0);
        Tags.Set("pump.run", true);
        Tags.Set("pump.speed", 50.0);
        Starter.LoadPercent = 100.0f;
        Tags.Set("starter.coil", true);

        // Inrush first: ten ticks after the coil, the motor draws several
        // times its rating, far past the top of a 0..12 A span.
        Run(10);
        if (mode != AnalogSignalMode.Engineering)
        {
            Expect(Starter.Current > 14.2f, $"{mode}: the starter is in inrush ({Starter.Current:0.0} A)");
            ExpectCount("starter.current", OverflowCode,
                        $"{mode}: starting inrush beyond 117.6 % of span reads as overflow", 0);
        }

        // Then settle: the plate's time constant is 1/0.3 s and the meter's
        // damping a quarter second, so forty seconds is many of both.
        Run(2400);
        CheckPoint(mode, "tank", "tank.level", 50.0, "half full", processTolerance: 0.5);
        CheckPoint(mode, "oven", "oven.temperature", 150.0, "50 % power, settled", processTolerance: 0.01);
        CheckPoint(mode, "flow", "flow.rate", 20.0, "pump at 50 %", processTolerance: 0.001);
        CheckPoint(mode, "starter", "starter.current", 6.0, "rated load, inrush over", processTolerance: 0.001);
        if (mode != AnalogSignalMode.Engineering)
        {
            ExpectCount("oven.temperature", FullScale / 2, $"{mode}: 150 degC on a 0..300 span");
            ExpectCount("flow.rate", FullScale / 2, $"{mode}: 20 L/min on a 0..40 span");
            ExpectCount("starter.current", FullScale / 2, $"{mode}: 6 A on a 0..12 span");
        }

        // --- 100 %
        Tags.Set("tank.fill", 100.0);
        Tags.Set("oven.heater", 100.0);
        Tags.Set("pump.speed", 100.0);
        Starter.LoadPercent = 200.0f;
        Run(2400);
        CheckPoint(mode, "tank", "tank.level", 100.0, "full", processTolerance: 0.0);
        CheckPoint(mode, "oven", "oven.temperature", 300.0, "full power, settled", processTolerance: 0.01);
        CheckPoint(mode, "flow", "flow.rate", 40.0, "pump at 100 %", processTolerance: 0.001);
        CheckPoint(mode, "starter", "starter.current", 12.0, "twice rated load", processTolerance: 0.001);
        if (mode != AnalogSignalMode.Engineering)
        {
            // The tank clamps at exactly 100 %, so this one is exact.
            ExpectCount("tank.level", FullScale, $"{mode}: a full tank on a 0..100 % span", 0);
            ExpectCount("oven.temperature", FullScale, $"{mode}: 300 degC on a 0..300 span");
            ExpectCount("flow.rate", FullScale, $"{mode}: 40 L/min on a 0..40 span");
            ExpectCount("starter.current", FullScale, $"{mode}: 12 A on a 0..12 span");
        }

        // Leave the machines idle for whatever comes next.
        Tags.Set("tank.fill", 0.0);
        Tags.Set("oven.heater", 0.0);
        Tags.Set("pump.run", false);
        Tags.Set("pump.speed", 0.0);
        Tags.Set("starter.coil", false);
        Starter.LoadPercent = 100.0f;
    }

    /// <summary>
    /// One channel at one point. The process must really be there (within
    /// <paramref name="processTolerance"/>, which is about how precisely this
    /// test can steer a plant, not about the conversion), and the tag must say
    /// so in the channel's current form.
    /// </summary>
    private void CheckPoint(AnalogSignalMode mode, string id, string tagId, double target, string what,
                            double processTolerance = 0.0)
    {
        double reading = Reading(id);
        Expect(Math.Abs(reading - target) <= processTolerance + 1e-6,
               $"{mode}: {id} {what}: the process reads {reading:0.####}, not {target}");

        if (mode == AnalogSignalMode.Engineering)
        {
            // What the part always published: the float reading, to within
            // the table's own epsilon. The bus does not store a change
            // smaller than Tag.FloatEpsilon (docs/tag-bus.md), so a reading
            // still creeping by 1e-7 a tick legitimately sits that far from
            // the last value stored.
            double got = Convert.ToDouble(Tags.Visible(tagId));
            Expect(Tags.Get(tagId)?.Type == TagType.Float && Math.Abs(got - reading) <= Tag.FloatEpsilon,
                   $"{mode}: {id} {what}: {tagId} reads {got}, expected the reading {reading}");
            return;
        }

        var signal = SignalOf(Editor.NodeFor(id)!);
        ExpectCount(tagId, ExpectedRaw(reading, signal.RangeMin, signal.RangeMax), $"{mode}: {id} {what}");
        GD.Print($"  {mode,-8} {id,-8} {what,-26} reading {reading,10:0.####}  -> {tagId} = {Tags.Visible(tagId)}");
    }

    // ---------------------------------------------------------------- the S7 convention

    /// <summary>
    /// Beyond the nominal range: overrange counts past 27648, underrange counts
    /// below 0, and the two saturating codes beyond those. Driven through the
    /// range setting rather than an impossible process -- and a range edit must
    /// not re-declare the tag, or dragging the slider would re-announce the
    /// whole table on every step.
    /// </summary>
    private void S7ConventionChecks()
    {
        var oven = Oven;
        var signal = oven.TemperatureSignal;

        Editor.ResetItems();          // plate at exactly 0 degC
        Run(1);

        (float Min, float Max, int Want, string What)[] cases =
        {
            // 27648 x -10 / 290 = -953.4
            (10.0f, 300.0f, -953, "0 degC on a 10..300 span is underrange, below zero"),
            (100.0f, 300.0f, UnderflowCode, "0 degC on a 100..300 span is past the underrange: underflow"),
            // 27648 x 280 / 260 = 29774.8
            (-280.0f, -20.0f, 29775, "0 degC on a -280..-20 span is overrange, past 27648"),
            // 27648 x 200 / 100 = 55296, well past 32511
            (-200.0f, -100.0f, OverflowCode, "0 degC on a -200..-100 span is past the overrange: overflow"),
        };
        foreach (var (min, max, want, what) in cases)
        {
            ApplyRange(oven, min, max);
            Run(1);
            ExpectCount("oven.temperature", want, $"S7 convention: {what}", 0);
            GD.Print($"  S7 convention: {what} -> {Tags.Visible("oven.temperature")}");
        }

        ApplyRange(oven, 0.0f, 300.0f);
        Run(1);
        Expect(signal.RangeMin == 0.0f && signal.RangeMax == 300.0f, "the oven's span is restored");
    }

    /// <summary>A range change the way the panel makes one: the part's own
    /// settings, then the editor asked whether that changed the I/O.</summary>
    private void ApplyRange(Node3D part, float min, float max)
    {
        var settings = new PartSettings();
        settings.Put("range_min", min);
        settings.Put("range_max", max);
        ((IPart)part).ApplySettings(settings);
        int before = _tagsChanged;
        bool redeclared = Editor.PartSettingsChanged(part);
        Expect(!redeclared && _tagsChanged == before,
               $"a range edit on '{part.Name}' re-declared its tags; only the signal type should");
    }

    // ---------------------------------------------------------------- wire break

    /// <summary>
    /// At 4-20 mA a broken wire reads 32767 (7FFFh), whatever the process is
    /// doing, for as long as it is broken, and the true reading returns the
    /// tick it is mended. Injected by forcing the contact, which is how every
    /// other fault in the library is injected.
    /// </summary>
    private void WireBreakChecks()
    {
        // Somewhere in the middle of the range, so neither 0 nor 27648 can be
        // mistaken for the right answer.
        Editor.ResetItems();
        Tags.Set("oven.heater", 50.0);
        Run(2400);
        Tags.Set("oven.heater", 0.0);

        foreach (var (id, tagId, _, _) in Channels)
        {
            if (id == "scale") continue;       // real frames; see ScaleWireBreak
            string wire = $"{id}.wirebreak";
            Expect(Tags.Force(wire, true), $"{wire} can be forced");
            Run(1);
            ExpectCount(tagId, OverflowCode, $"4-20 mA: {id} with its wire broken reads 7FFFh", 0);
            GD.Print($"  wire break: {tagId} = {Tags.Visible(tagId)} while {wire} is forced");
            Expect(SignalOf(Editor.NodeFor(id)!).WireBroken, $"{id}'s channel knows its wire is broken");

            Tags.ClearForce(wire);
            Run(1);
            var signal = SignalOf(Editor.NodeFor(id)!);
            ExpectCount(tagId, ExpectedRaw(Reading(id), signal.RangeMin, signal.RangeMax),
                        $"4-20 mA: {id} reads its measurement again once the wire is mended");
            Expect(RawOf(tagId) != OverflowCode, $"4-20 mA: {id} no longer reads 7FFFh after the repair");
        }

        // The oven was mid-range for that, so its restored value is a real
        // number rather than a zero that a stuck 0 would also produce.
        Expect(RawOf("oven.temperature") is > 100 and < FullScale,
               $"the oven's restored count ({RawOf("oven.temperature")}) is mid-range");
    }

    // ---------------------------------------------------------------- load cell, on real frames

    private int _cartonsSpawned;
    private AnalogSignalMode _scaleMode;

    /// <summary>Drop a short carton onto the stopped deck, left of centre for
    /// the first and right for the second, both well inside the load cell's
    /// area.</summary>
    private void SpawnCarton()
    {
        var box = new BoxPhysics { IsTall = false };
        Editor.GetParent().AddChild(box);
        float x = _cartonsSpawned == 0 ? -0.35f : 0.35f;
        box.GlobalPosition = new Vector3(x, PartLayout.WorkPlaneY + 0.14f, 4.0f);
        _cartonsSpawned++;
    }

    /// <summary>The published load against the part's own reading, and --
    /// once the span is two cartons -- against the bare fraction of 27648.</summary>
    private void CheckScale(double targetFraction, string what)
    {
        var scale = Scale;
        var signal = scale.WeightSignal;
        if (_scaleMode == AnalogSignalMode.Engineering)
        {
            Expect(Tags.Get("scale.weight")?.Type == TagType.Int
                   && RawOf("scale.weight") == (int)scale.MeasuredWeight,
                   $"engineering: the load cell publishes grams as it always did, {what} "
                   + $"({RawOf("scale.weight")} vs {(int)scale.MeasuredWeight})");
            return;
        }

        ExpectCount("scale.weight", ExpectedRaw(scale.MeasuredWeight, signal.RangeMin, signal.RangeMax),
                    $"{_scaleMode}: load cell, {what}");
        GD.Print($"  {_scaleMode,-8} scale    {what,-26} reading {scale.MeasuredWeight,10:0.####} g "
                 + $"({scale.CartonsOnScale} cartons, span {signal.RangeMin}..{signal.RangeMax}) -> scale.weight = {Tags.Visible("scale.weight")}");
        ExpectCount("scale.weight", (int)Math.Round(FullScale * targetFraction),
                    $"{_scaleMode}: load cell, {what}, against the bare number");
    }

    /// <summary>The deck is empty -- read 0 % -- and the first carton goes
    /// on.</summary>
    private void ScaleEmptyThenLoad(AnalogSignalMode mode)
    {
        _scaleMode = mode;
        _cartonsSpawned = 0;
        Tags.Set("scale.rotate", false);
        Expect(Scale.CartonsOnScale == 0 && Scale.MeasuredWeight == 0.0f,
               $"{mode}: the deck is empty after the reset ({Scale.CartonsOnScale} cartons, {Scale.MeasuredWeight} g)");
        Run(1);
        CheckScale(0.0, "empty deck");
        if (mode != AnalogSignalMode.Engineering) ExpectCount("scale.weight", 0, $"{mode}: an empty deck reads 0", 0);
        SpawnCarton();
    }

    /// <summary>One carton on an engineering-mode deck: what it weighs becomes
    /// the unit of the span, 0..two cartons.</summary>
    private void CalibrateScale()
    {
        Expect(Scale.CartonsOnScale == 1, $"a carton landed on the deck (got {Scale.CartonsOnScale})");
        Run(1);
        CheckScale(0.5, "one carton");
        _cartonGrams = Scale.MeasuredWeight;
        Expect(_cartonGrams > 100.0f, $"a carton weighs something ({_cartonGrams} g)");
        ApplyRange(Scale, 0.0f, 2.0f * _cartonGrams);
    }

    private void ScaleOneThenTwo()
    {
        Expect(Scale.CartonsOnScale == 1, $"{_scaleMode}: one carton on the deck (got {Scale.CartonsOnScale})");
        Run(1);
        CheckScale(0.5, "one carton, half of a two-carton span");
        SpawnCarton();
    }

    private void ScaleFull()
    {
        Expect(Scale.CartonsOnScale == 2, $"{_scaleMode}: two cartons on the deck (got {Scale.CartonsOnScale})");
        Run(1);
        CheckScale(1.0, "two cartons, the whole span");
    }

    /// <summary>The load cell's own wire, on a full deck.</summary>
    private void ScaleWireBreak()
    {
        Expect(Tags.Force("scale.wirebreak", true), "the load cell's wire break can be forced");
        Run(1);
        ExpectCount("scale.weight", OverflowCode, "4-20 mA: the load cell with its wire broken reads 7FFFh", 0);
        Tags.ClearForce("scale.wirebreak");
        Run(1);
        ExpectCount("scale.weight", FullScale, "4-20 mA: the load cell reads two cartons again once mended");
    }

    // ---------------------------------------------------------------- save, load, and prove it

    private void SaveAndReload()
    {
        foreach (var (id, _, _, _) in Channels)
        {
            var saved = PartProperties.Capture(Editor.NodeFor(id)!);
            Expect(saved.TryGetValue("signal", out var key) && key == "ma_4_20",
                   $"{id}: the captured settings carry signal=ma_4_20 (got '{(saved.TryGetValue("signal", out var k) ? k : "nothing")}')");
        }

        Expect(Editor.SaveSceneToFile(RoundTripPath), "the scene saves");
        string text = "";
        using (var file = Godot.FileAccess.Open(RoundTripPath, Godot.FileAccess.ModeFlags.Read))
        {
            if (file is not null) text = file.GetAsText();
        }
        int written = text.Split("\"ma_4_20\"").Length - 1;
        Expect(written == Channels.Length, $"the saved file names ma_4_20 {written} times, not {Channels.Length}");

        Expect(Editor.LoadSceneFromFile(RoundTripPath), "the saved scene loads back");
    }

    private void CheckReloaded()
    {
        CheckDeclared(AnalogSignalMode.Ma4To20);

        var oven = Oven;
        Expect(oven.TemperatureSignal.RangeMin == 0.0f && oven.TemperatureSignal.RangeMax == 300.0f,
               $"the oven's span survives the round trip ({oven.TemperatureSignal.RangeMin}..{oven.TemperatureSignal.RangeMax})");
        Expect(Starter.CurrentSignal.RangeMax == 12.0f,
               $"the starter's span survives the round trip (max {Starter.CurrentSignal.RangeMax})");
        Expect(Math.Abs(Scale.WeightSignal.RangeMax - 2.0f * _cartonGrams) < 0.5f,
               $"the load cell's span survives the round trip ({Scale.WeightSignal.RangeMax} g, set to {2.0f * _cartonGrams} g)");

        // The effect, not the dictionary: 150 degC is half of the reloaded
        // 0..300 span. Had the span fallen back to the 0..400 default it
        // would read 10368, and had the mode fallen back to engineering the
        // tag would not be an INT at all.
        Tags.Set("oven.heater", 50.0);
        Run(2400);
        ExpectCount("oven.temperature", FullScale / 2, "after reloading, 150 degC reads half of the saved 0..300 span");
        Tags.Set("oven.heater", 0.0);

        // And the reloaded wire break still works: the per-part tag cache is
        // built from what this part declares, not from its type's default.
        Expect(Tags.Force("tank.wirebreak", true), "the reloaded tank's wire break can be forced");
        Run(1);
        ExpectCount("tank.level", OverflowCode, "a reloaded 4-20 mA tank with its wire broken reads 7FFFh", 0);
        Tags.ClearForce("tank.wirebreak");
    }

    // ---------------------------------------------------------------- panel plumbing

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

    private void Finish()
    {
        if (_done) return;
        _done = true;   // gotcha 13: Quit lands at the end of the frame

        if (_failures.Count == 0)
        {
            GD.Print("self-test analog: PASS");
            GetTree().Quit(0);
            return;
        }
        GD.PrintErr($"self-test analog: FAIL ({_failures.Count})");
        foreach (var failure in _failures) GD.PrintErr($"  - {failure}");
        GetTree().Quit(1);
    }
}
