using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// IP-28, the other half of <c>--self-test=analog</c>: the analog
/// <em>outputs</em> -- tank valves, heater power, pump, fan and VFD speed
/// references, the gantry target, the gauge -- and the seven inputs IP-16 left
/// in engineering units: pump flow, fan airflow, VFD actual speed, gantry
/// position, light-curtain height, lift height and the panel's setpoint pot.
///
/// Every output is driven the way a PLC drives it, by writing the tag -- a
/// float in engineering mode, a count in the raw modes -- and judged by what
/// the <em>actuator</em> then does (the valve's opening, the drive's reference,
/// the needle), plus, at half scale, by what the process does with that
/// (the tank's fill rate, the plate's settled temperature, the pump's flow).
/// The counts are this file's own arithmetic: 27648 written out, not borrowed
/// from <see cref="AnalogOutput"/>.
///
/// The card's codes are checked for every output: overrange stops at the
/// actuator's end stop, and overflow (32512 and up, 7FFFh included) switches
/// the channel off, which leaves the actuator at the bottom of its span.
/// </summary>
public partial class AnalogSignalSelfTest
{
    /// <summary>The inputs IP-28 added: part id, measurement tag, part type and
    /// the tail of the engineering-mode name every existing program knows it
    /// by.</summary>
    private static readonly (string Id, string Tag, string Type, string EngName)[] NewInputs =
    {
        ("pump", "pump.flow", "DosingPump", "Flow (L/min)"),
        ("fan", "fan.airflow", "CoolingFan", "Airflow (%)"),
        ("vfd", "vfd.actual", "VariableConveyor", "Actual Speed (%)"),
        ("gantry", "gantry.position", "PickPlaceArm", "Position (%)"),
        ("curtain", "curtain.height", "LightArray", "Height (m)"),
        ("lift", "lift.height", "VerticalLift", "Height (m)"),
        ("panel", "panel.setpoint", "ButtonPanel", "Setpoint"),
    };

    /// <summary>Every analog output, one row per tag.</summary>
    private static readonly (string Id, string Tag, string Type, string EngName)[] Outputs =
    {
        ("tank", "tank.fill", "LevelTank", "Fill Valve (%)"),
        ("tank", "tank.drain", "LevelTank", "Drain Valve (%)"),
        ("oven", "oven.heater", "HeatingStation", "Power (%)"),
        ("pump", "pump.speed", "DosingPump", "Speed Ref (%)"),
        ("fan", "fan.speed", "CoolingFan", "Speed Ref (%)"),
        ("vfd", "vfd.speed", "VariableConveyor", "Speed Ref (%)"),
        ("gantry", "gantry.target", "PickPlaceArm", "Target (%)"),
        ("gauge", "gauge.value", "AnalogGauge", "Value"),
    };

    /// <summary>One Output Signal setting per part: the tank's two valves
    /// share theirs.</summary>
    private static readonly (string Id, string Type)[] OutputParts =
    {
        ("tank", "LevelTank"), ("oven", "HeatingStation"), ("pump", "DosingPump"), ("fan", "CoolingFan"),
        ("vfd", "VariableConveyor"), ("gantry", "PickPlaceArm"), ("gauge", "AnalogGauge"),
    };

    /// <summary>The gauge's plate in this scene. Not the 0..100 default, so a
    /// raw count that ignored the plate would be caught.</summary>
    private const float GaugeMax = 300.0f;

    /// <summary>The lift's position transducer spanned over the mast's three
    /// levels exactly, so levels 0, 1 and 2 are 0, 50 and 100 % of it.</summary>
    private const float LiftSpan = 1.8f;

    /// <summary>What the parked tall carton measures in engineering mode; the
    /// raw phases span the curtain to it.</summary>
    private float _curtainReading;
    private BoxPhysics? _curtainCarton;

    private void AddIp28Parts(SceneData data)
    {
        // Far from everything IP-16's phases drive, and from each other: a fan
        // cools any heating station in reach, and nothing here should.
        data.Parts.Add(At("fan", "CoolingFan", 8.0f, -8.0f));
        data.Parts.Add(At("vfd", "VariableConveyor", 8.0f, 4.0f));
        data.Parts.Add(At("gantry", "PickPlaceArm", 8.0f, 0.0f));
        data.Parts.Add(At("gauge", "AnalogGauge", 8.0f, -12.0f, new()
        {
            ["scale_min"] = "0", ["scale_max"] = "300", ["alarm_at"] = "1000",
        }));
        data.Parts.Add(At("curtain", "LightArray", 16.0f, -8.0f));
        data.Parts.Add(At("lift", "VerticalLift", 16.0f, 0.0f, new()
        {
            ["levels"] = "3", ["spacing"] = "0.9", ["range_min"] = "0", ["range_max"] = "1.8",
        }));
        data.Parts.Add(At("panel", "ButtonPanel", 16.0f, -12.0f));
    }

    private DosingPump Pump => Part<DosingPump>("pump");
    private CoolingFan Fan => Part<CoolingFan>("fan");
    private VariableConveyor Vfd => Part<VariableConveyor>("vfd");
    private PickPlaceArm Gantry => Part<PickPlaceArm>("gantry");
    private AnalogGauge Gauge => Part<AnalogGauge>("gauge");
    private LightArray Curtain => Part<LightArray>("curtain");
    private VerticalLift Lift => Part<VerticalLift>("lift");
    private ButtonPanel Panel => Part<ButtonPanel>("panel");

    private AnalogSignal InputSignalOf(string id) => id switch
    {
        "pump" => Pump.FlowSignal,
        "fan" => Fan.AirflowSignal,
        "vfd" => Vfd.ActualSignal,
        "gantry" => Gantry.PositionSignal,
        "curtain" => Curtain.HeightSignal,
        "lift" => Lift.HeightSignal,
        "panel" => Panel.SetpointSignal,
        "tank" => TankPart.LevelSignal,
        "oven" => Oven.TemperatureSignal,
        _ => throw new InvalidOperationException($"{id} has no analog input under test"),
    };

    private AnalogOutput OutputSignalOf(string id) => id switch
    {
        "tank" => TankPart.ValveSignal,
        "oven" => Oven.PowerSignal,
        "pump" => Pump.SpeedSignal,
        "fan" => Fan.SpeedSignal,
        "vfd" => Vfd.SpeedSignal,
        "gantry" => Gantry.TargetSignal,
        "gauge" => Gauge.ValueSignal,
        _ => throw new InvalidOperationException($"{id} has no analog output"),
    };

    private double InputReading(string id) => id switch
    {
        "pump" => Pump.Flow,
        "fan" => Fan.Airflow,
        "vfd" => Vfd.ActualPercent,
        "gantry" => Gantry.AxisPosition,
        "curtain" => Curtain.MeasuredHeight,
        "lift" => Lift.Height,
        "panel" => Panel.Setpoint,
        _ => throw new InvalidOperationException(id),
    };

    /// <summary>What the actuator behind an output is doing, in its own units:
    /// the opening the valve integrated, the power the element was given, the
    /// reference the drive is chasing, the position the axis is aimed at, the
    /// reading under the needle.</summary>
    private double Observed(string tagId) => tagId switch
    {
        "tank.fill" => TankPart.FillOpening,
        "tank.drain" => TankPart.DrainOpening,
        "oven.heater" => Oven.CommandedPower,
        "pump.speed" => Pump.CommandedSpeed,
        "fan.speed" => Fan.CommandedSpeed,
        "vfd.speed" => Vfd.ReferencePercent,
        "gantry.target" => Gantry.Target,
        "gauge.value" => Gauge.Value,
        _ => throw new InvalidOperationException(tagId),
    };

    private static double SpanMax(string tagId) => tagId == "gauge.value" ? GaugeMax : 100.0;

    /// <summary>A PLC's write for <paramref name="percent"/> of an output's
    /// span: the engineering float by default, the count in raw.</summary>
    private static object Command(AnalogSignalMode mode, string tagId, double percent) =>
        // Both arms boxed explicitly: a bare conditional would widen the count
        // to a double, which is exactly the write an INT tag refuses.
        mode == AnalogSignalMode.Engineering
            ? (object)(SpanMax(tagId) * percent / 100.0)
            : (object)(int)Math.Round(FullScale * percent / 100.0, MidpointRounding.AwayFromZero);

    private void ExpectObserved(string tagId, double percent, string what)
    {
        double want = SpanMax(tagId) * percent / 100.0;
        double got = Observed(tagId);
        Expect(Math.Abs(got - want) <= 1e-3 * SpanMax(tagId),
               $"{what}: {tagId} = {Tags.Visible(tagId)} left the actuator at {got:0.####}, expected {want:0.####}");
    }

    private void SetOutputs(AnalogSignalMode mode, double percent)
    {
        foreach (var (_, tagId, _, _) in Outputs) Tags.Set(tagId, Command(mode, tagId, percent));
    }

    // ---------------------------------------------------------------- declarations

    /// <summary>Type, kind and name of every IP-28 tag, as the table holds it
    /// and as a driver's describe carries it; and the wire-break contact only
    /// where a 4-20 mA <em>input</em> justifies one.</summary>
    private void CheckDeclaredIp28(AnalogSignalMode outputs, AnalogSignalMode inputs)
    {
        JsonArray describe = Tags.ToJson();

        void One(string tagId, string engName, TagKind kind, AnalogSignalMode mode, string label)
        {
            var tag = Tags.Get(tagId);
            if (tag is null) { Expect(false, $"{label}: {tagId} vanished"); return; }
            TagType want = mode == AnalogSignalMode.Engineering ? TagType.Float : TagType.Int;
            Expect(tag.Type == want, $"{label}: {tagId} is declared {tag.Type}, expected {want}");
            Expect(tag.Kind == kind, $"{label}: {tagId} is an {tag.Kind}, expected {kind}");
            bool raw = mode != AnalogSignalMode.Engineering;
            Expect(tag.Name.Contains("raw") == raw, $"{label}: {tagId} is named '{tag.Name}'");
            if (!raw)
                Expect(tag.Name.EndsWith(engName),
                       $"{label}: engineering keeps {tagId}'s old name ('{tag.Name}' should end '{engName}')");

            JsonObject? entry = null;
            foreach (var node in describe)
                if (node is JsonObject o && (string?)o["id"] == tagId) entry = o;
            string wantName = raw ? "int" : "float";
            Expect(entry is not null && (string?)entry["type"] == wantName,
                   $"{label}: the describe says {tagId} is "
                   + $"{(entry is null ? "absent" : (string?)entry["type"])}, not {wantName}");
            if (entry is not null && raw)
                Expect(!entry["value"]!.ToJsonString().Contains('.'),
                       $"{label}: {tagId}'s describe value {entry["value"]!.ToJsonString()} is not an integer");
        }

        foreach (var (id, tagId, _, engName) in Outputs)
        {
            One(tagId, engName, TagKind.Output, outputs, $"outputs {outputs}");
            Expect(OutputSignalOf(id).Mode == outputs, $"{id}'s output reports {OutputSignalOf(id).Mode}");
        }
        foreach (var (id, tagId, _, engName) in NewInputs)
        {
            One(tagId, engName, TagKind.Input, inputs, $"inputs {inputs}");
            Expect(InputSignalOf(id).Mode == inputs, $"{id}'s input reports {InputSignalOf(id).Mode}");
            bool wantsBreak = inputs == AnalogSignalMode.Ma4To20;
            Expect(Tags.Contains($"{id}.wirebreak") == wantsBreak,
                   $"inputs {inputs}: {id}.wirebreak {(wantsBreak ? "is missing" : "exists")}");
        }
        // An output alone never has one, in any mode.
        Expect(!Tags.Contains("gauge.wirebreak"),
               $"outputs {outputs}: the gauge, which has no analog input, grew a wire-break contact");
    }

    /// <summary>
    /// Outputs first, then inputs, through the panel's own dropdowns. Between
    /// the two, with every output at 4-20 mA and the IP-28 inputs not yet, no
    /// part may carry a wire break its input does not justify: the AO side has
    /// none (see <see cref="AnalogOutput"/>).
    /// </summary>
    private void SwitchIp28(AnalogSignalMode mode)
    {
        int before = _tagsChanged;
        foreach (var (id, type) in OutputParts) ChooseOn(AnalogOutput.Label, id, type, mode);
        Expect(_tagsChanged - before == OutputParts.Length,
               $"switching {OutputParts.Length} parts' outputs to {mode} announced {_tagsChanged - before} changes");

        foreach (var (id, _) in OutputParts)
        {
            bool inputIsLoop = id is "tank" or "oven"
                ? InputSignalOf(id).Mode == AnalogSignalMode.Ma4To20
                : id != "gauge" && InputSignalOf(id).Mode == AnalogSignalMode.Ma4To20;
            if (!inputIsLoop)
                Expect(!Tags.Contains($"{id}.wirebreak"),
                       $"outputs at {mode}: {id}.wirebreak exists, but no output has a wire break to report");
        }

        before = _tagsChanged;
        foreach (var (id, _, type, _) in NewInputs) Choose(id, type, mode);
        Expect(_tagsChanged - before == NewInputs.Length,
               $"switching {NewInputs.Length} inputs to {mode} announced {_tagsChanged - before} changes");

        CheckDeclaredIp28(mode, mode);

        // No range rows where the part owns the span: an output's is its
        // actuator's full scale, the pot's is its plate.
        _panel.InspectNode(Editor.NodeFor("gauge")!, "gauge", "AnalogGauge");
        Expect(FindSettingControl<Control>("Range Min") is null,
               $"{mode}: the gauge offers a Range Min row; its plate is its span");
        _panel.InspectNode(Editor.NodeFor("panel")!, "panel", "ButtonPanel");
        Expect(FindSettingControl<OptionButton>("Signal") is not null,
               "the panel offers a Signal dropdown for its pot");
        Expect(FindSettingControl<Control>("Range Min") is null,
               $"{mode}: the panel offers a Range Min row; its plate is its span");
        _panel.InspectNode(Editor.NodeFor("pump")!, "pump", "DosingPump");
        Expect(FindSettingControl<Control>("Range Min") is not null,
               $"{mode}: the pump's flow transmitter offers its span once raw");
    }

    // ---------------------------------------------------------------- engineering

    /// <summary>The default is what every existing program was written
    /// against: the same floats, names and effects.</summary>
    private void OutputsEngineeringPhase()
    {
        CheckDeclaredIp28(AnalogSignalMode.Engineering, AnalogSignalMode.Engineering);

        int before = _tagsChanged;
        ChooseOn(AnalogOutput.Label, "tank", "LevelTank", AnalogSignalMode.Engineering);
        Expect(_tagsChanged == before, "re-choosing the output mode a part is already in announces nothing");

        OutputsProcessChecks(AnalogSignalMode.Engineering);

        // A click on the fill valve still forces the percent it always did.
        Operate("tank", "fill");
        Expect(Tags.Visible("tank.fill") is double open && open == 100.0,
               $"engineering: a click opens the fill valve to 100.0 (got {Tags.Visible("tank.fill")})");
        Operate("tank", "fill");
        Expect(Tags.Visible("tank.fill") is double shut && shut == 0.0,
               $"engineering: a second click shuts it (got {Tags.Visible("tank.fill")})");
        Tags.ClearForce("tank.fill");

        ParkCurtainCarton();
    }

    // ---------------------------------------------------------------- 0 / 50 / 100 %

    /// <summary>
    /// Every output at 0, 50 and 100 % of its span, in the mode under test,
    /// judged by the actuator; every IP-28 input at 0, 50 and 100 % of its
    /// span, judged by the count -- most of them driven there by the outputs,
    /// so a conversion wrong in either direction shows. The curtain is on
    /// frames, separately.
    /// </summary>
    private void OutputsProcessChecks(AnalogSignalMode mode)
    {
        Editor.ResetItems();
        Run(1);

        // The tank, by its effect: half a fill valve for one second adds half
        // the fill rate's worth of level.
        Tags.Set("tank.drain", Command(mode, "tank.drain", 0.0));
        Tags.Set("tank.fill", Command(mode, "tank.fill", 50.0));
        Run(60);
        double wantLevel = TankPart.FillRate * 0.5 * 60 * Tick;
        Expect(Math.Abs(TankPart.Level - wantLevel) < 0.05,
               $"{mode}: half a fill valve for a second fills {TankPart.Level:0.###} %, expected {wantLevel:0.###} %");
        Tags.Set("tank.fill", Command(mode, "tank.fill", 0.0));

        Tags.Set("pump.run", true);
        Tags.Set("fan.run", true);
        Tags.Set("vfd.run", true);

        foreach (double percent in new[] { 0.0, 50.0, 100.0 })
        {
            SetOutputs(mode, percent);
            Tags.Set("lift.target", (int)(percent / 50.0));
            Panel.SetSetpoint((float)percent);

            // 40 s at half scale, for the plate's thermal time constant; 10 s
            // is many of every other ramp here.
            Run(percent == 50.0 ? 2400 : 600);

            string at = $"{mode}, outputs at {percent} %";
            foreach (var (_, tagId, _, _) in Outputs) ExpectObserved(tagId, percent, at);

            double fraction = percent / 100.0;
            CheckIp28Input(mode, "pump", "pump.flow", 40.0 * fraction, fraction, at);
            CheckIp28Input(mode, "fan", "fan.airflow", percent, fraction, at);
            CheckIp28Input(mode, "vfd", "vfd.actual", percent, fraction, at);
            CheckIp28Input(mode, "gantry", "gantry.position", percent, fraction, at);
            CheckIp28Input(mode, "lift", "lift.height", LiftSpan * fraction, fraction, at, processTolerance: 1e-4);
            CheckIp28Input(mode, "panel", "panel.setpoint", percent, fraction, at);

            if (percent == 50.0)
            {
                // What the half-power command does to the process: 90 x 0.5 /
                // 0.3 = 150 degC, which the IP-16 half already asserts in
                // engineering -- here it is reached through a raw count too.
                Expect(Math.Abs(Oven.Temperature - 150.0) < 0.05,
                       $"{at}: the plate settles at {Oven.Temperature:0.###} degC, expected 150");
                Expect(Math.Abs(Gauge.RotationOfNeedleDegrees) < 1.0f,
                       $"{at}: the gauge's needle stands at {Gauge.RotationOfNeedleDegrees:0.#} deg, not upright");
            }
        }

        // Idle for whatever comes next.
        SetOutputs(mode, 0.0);
        Tags.Set("pump.run", false);
        Tags.Set("fan.run", false);
        Tags.Set("vfd.run", false);
        Tags.Set("lift.target", 0);
        Panel.SetSetpoint(50.0f);
        Run(600);
    }

    /// <summary>One IP-28 input at one point: the process really there, and
    /// the tag saying so in its current form -- against the part's reading and,
    /// in raw, against the bare fraction of 27648.</summary>
    private void CheckIp28Input(AnalogSignalMode mode, string id, string tagId, double target, double fraction,
                                string what, double processTolerance = 1e-3)
    {
        double reading = InputReading(id);
        Expect(Math.Abs(reading - target) <= processTolerance,
               $"{what}: {id} reads {reading:0.####}, expected {target:0.####}");

        if (mode == AnalogSignalMode.Engineering)
        {
            double got = Convert.ToDouble(Tags.Visible(tagId));
            Expect(Tags.Get(tagId)?.Type == TagType.Float && Math.Abs(got - reading) <= Tag.FloatEpsilon,
                   $"{what}: {tagId} reads {got}, expected the reading {reading}");
            return;
        }

        var signal = InputSignalOf(id);
        ExpectCount(tagId, ExpectedRaw(reading, signal.RangeMin, signal.RangeMax), $"{what}: {id}");
        ExpectCount(tagId, (int)Math.Round(FullScale * fraction), $"{what}: {id}, against the bare number");
    }

    // ---------------------------------------------------------------- the card's codes

    /// <summary>
    /// Every output, every region of the S7-1500 output table, in this file's
    /// own terms: percent of span the actuator must end up at.
    /// </summary>
    private void OutputCodeChecks()
    {
        (int Count, double Percent, string What)[] codes =
        {
            (0, 0.0, "0 counts is the bottom of the span"),
            (1, 100.0 / 27648.0, "one count is one step"),
            (6912, 25.0, "6912 is a quarter"),
            (27647, 100.0 * 27647.0 / 27648.0, "27647 is one step short of full"),
            (27648, 100.0, "27648 is full scale"),
            (27649, 100.0, "overrange: the actuator is at its end stop"),
            (30000, 100.0, "deep overrange: still the end stop"),
            (32511, 100.0, "the top of the overrange"),
            (32512, 0.0, "overflow: the card switches the channel off"),
            (32767, 0.0, "7FFFh: overflow, output off"),
            (100000, 0.0, "a DInt past any card's range: overflow, output off"),
            (-1, 0.0, "underrange: below the bottom of the span"),
            (-6912, 0.0, "-6912, 0 mA on a 4-20 mA output"),
            (-32768, 0.0, "8000h: underflow"),
            (-100000, 0.0, "a DInt below any card's range"),
        };

        foreach (var (_, tagId, _, _) in Outputs)
        {
            foreach (var (count, percent, what) in codes)
            {
                Tags.Set(tagId, count);
                Run(1);
                ExpectObserved(tagId, percent, $"{OutputSignalOf(tagId.Split('.')[0]).Mode}: {what} ({count})");
            }
            Tags.Set(tagId, 0);
        }
        Run(1);

        PlateSpanChecks();
    }

    /// <summary>Where the span is the plate, moving the plate moves what a
    /// count means -- on the gauge's output and on the pot's input.</summary>
    private void PlateSpanChecks()
    {
        var gauge = Gauge;
        gauge.ConfigureScale(0.0f, 600.0f, gauge.AlarmAt, gauge.Unit);
        Tags.Set("gauge.value", FullScale / 2);
        Run(1);
        Expect(Math.Abs(gauge.Value - 300.0f) < 1e-3f,
               $"half scale on a 0..600 plate reads {gauge.Value} on the gauge, expected 300");
        gauge.ConfigureScale(0.0f, GaugeMax, gauge.AlarmAt, gauge.Unit);
        Tags.Set("gauge.value", 0);

        var panel = Panel;
        panel.ConfigureSetpoint(0.0f, 200.0f, panel.SetpointUnit, 150.0f);
        Run(1);
        // 27648 x 150 / 200 = 20736
        ExpectCount("panel.setpoint", 20736, "the pot at 150 on a 0..200 plate", 0);

        // The pot goes both ways: a count forced over the wire turns the knob
        // to what that count means on the plate. 27648 x 50 / 200 = 6912.
        Tags.Force("panel.setpoint", 6912);
        Run(1);
        Expect(Math.Abs(panel.Setpoint - 50.0f) < 1e-3f,
               $"a forced 6912 on a 0..200 plate turns the knob to {panel.Setpoint}, expected 50");
        Tags.ClearForce("panel.setpoint");
        panel.ConfigureSetpoint(0.0f, 100.0f, panel.SetpointUnit, 50.0f);
        Run(1);
    }

    // ---------------------------------------------------------------- 4-20 mA

    /// <summary>Each new 4-20 mA input's wire break reads 7FFFh and clears,
    /// from a mid-range reading so neither 0 nor 27648 can pass for the
    /// restored value. The curtain's is checked on its own frames.</summary>
    private void Ip28WireBreakChecks()
    {
        Tags.Set("pump.run", true);
        Tags.Set("fan.run", true);
        Tags.Set("vfd.run", true);
        SetOutputs(AnalogSignalMode.Ma4To20, 50.0);
        Tags.Set("lift.target", 1);
        Run(600);

        foreach (var (id, tagId, _, _) in NewInputs)
        {
            if (id == "curtain") continue;
            string wire = $"{id}.wirebreak";
            Expect(Tags.Force(wire, true) || Tags.IsForced(wire), $"{wire} can be forced");
            Run(1);
            ExpectCount(tagId, OverflowCode, $"4-20 mA: {id} with its wire broken reads 7FFFh", 0);
            Expect(InputSignalOf(id).WireBroken, $"{id}'s channel knows its wire is broken");
            Tags.ClearForce(wire);
            Run(1);
            var signal = InputSignalOf(id);
            ExpectCount(tagId, ExpectedRaw(InputReading(id), signal.RangeMin, signal.RangeMax),
                        $"4-20 mA: {id} reads its measurement again once mended");
            Expect(RawOf(tagId) is > 100 and < FullScale,
                   $"4-20 mA: {id}'s restored count ({RawOf(tagId)}) is mid-range");
        }

        SetOutputs(AnalogSignalMode.Ma4To20, 0.0);
        Tags.Set("pump.run", false);
        Tags.Set("fan.run", false);
        Tags.Set("vfd.run", false);
        Tags.Set("lift.target", 0);
        Run(600);
    }

    // ---------------------------------------------------------------- the engine's own writes

    /// <summary>Operate a part exactly as <c>SceneEditor.OperatePart</c> does,
    /// minus the ray that picked it.</summary>
    private void Operate(string id, string region)
    {
        var node = Editor.NodeFor(id)!;
        var builder = new PartTagBuilder(null, id, 0);
        ((IPart)node).DeclareTags(builder);
        var ids = new Dictionary<string, string>();
        foreach (string suffix in builder.Suffixes) ids[suffix] = $"{id}.{suffix}";
        ((IPart)node).Operate(new PartOperate(Tags, ids, id, region, tagId => Tags.Set(tagId, true)));
    }

    /// <summary>
    /// A click and an IP-07 probe write outputs from inside the engine. On a
    /// raw output each must write a count, or the INT tag refuses it and the
    /// click throws.
    /// </summary>
    private void RawClickAndProbeChecks()
    {
        Operate("tank", "fill");
        Expect(Tags.Visible("tank.fill") is int open && open == FullScale,
               $"raw: a click opens the fill valve to 27648 (got {Tags.Visible("tank.fill")})");
        Run(1);
        Expect(TankPart.FillOpening == 100.0f, $"raw: and the valve opens fully ({TankPart.FillOpening} %)");
        Operate("tank", "fill");
        Expect(Tags.Visible("tank.fill") is int shut && shut == 0,
               $"raw: a second click shuts it (got {Tags.Visible("tank.fill")})");
        Tags.ClearForce("tank.fill");

        Operate("oven", "");
        Expect(Tags.Visible("oven.heater") is int on && on == FullScale,
               $"raw: a click puts the heater to 27648 (got {Tags.Visible("oven.heater")})");
        Tags.ClearForce("oven.heater");

        foreach (string id in new[] { "pump", "fan" })
        {
            Operate(id, "");
            Expect(Tags.Visible($"{id}.speed") is int speed && speed == FullScale,
                   $"raw: a click runs the {id} at 27648 (got {Tags.Visible($"{id}.speed")})");
            Tags.ClearForce($"{id}.speed");
            Tags.ClearForce($"{id}.run");
        }

        foreach (var (id, _) in OutputParts)
        {
            if (((IPart)Editor.NodeFor(id)!).Probe is not { } probe) continue;
            foreach (var (suffix, value) in probe.Drive)
            {
                var tag = Tags.Get($"{id}.{suffix}");
                if (tag is null || tag.Type != TagType.Int) continue;
                try { tag.Coerce(value); }
                catch (ArgumentException ex)
                {
                    Expect(false, $"raw: {id}'s IP-07 probe drives {suffix} with {value}, which its INT refuses: {ex.Message}");
                }
            }
        }
        Expect(((IPart)TankPart).Probe?.Drive["fill"] is int full && full == FullScale,
               "raw: the tank's probe opens the fill valve with 27648");
    }

    // ---------------------------------------------------------------- the curtain, on frames

    /// <summary>A tall carton, parked and frozen across the beams, the way the
    /// scene self-test parks its probe cartons.</summary>
    private void ParkCurtainCarton()
    {
        var curtain = Curtain;
        var box = new BoxPhysics { Name = "CurtainCarton", IsTall = true };
        Editor.GetParent().AddChild(box);
        box.GlobalPosition = curtain.GlobalTransform
            * new Vector3(0, PartLayout.BeltSurface + box.Height / 2.0f, -curtain.Range / 2.0f);
        box.FreezeMode = RigidBody3D.FreezeModeEnum.Kinematic;
        box.Freeze = true;
        _curtainCarton = box;
    }

    private void CurtainCheck(AnalogSignalMode mode)
    {
        var curtain = Curtain;
        Expect(curtain.IsBlocked && curtain.MeasuredHeight > 0.2f,
               $"{mode}: the curtain measures the parked tall carton (blocked {curtain.IsBlocked}, {curtain.MeasuredHeight} m)");
        Run(1);

        if (mode == AnalogSignalMode.Engineering)
        {
            _curtainReading = curtain.MeasuredHeight;
            double got = Convert.ToDouble(Tags.Visible("curtain.height"));
            Expect(Tags.Get("curtain.height")?.Type == TagType.Float && Math.Abs(got - _curtainReading) <= Tag.FloatEpsilon,
                   $"engineering: curtain.height reads {got}, expected the reading {_curtainReading}");
        }
        else
        {
            Expect(curtain.MeasuredHeight == _curtainReading,
                   $"{mode}: the same carton measures {curtain.MeasuredHeight} m, not {_curtainReading} m");
            // Spanned so the carton is half, then all, of the range.
            ApplyRange(curtain, 0.0f, 2.0f * _curtainReading);
            Run(1);
            ExpectCount("curtain.height", FullScale / 2, $"{mode}: the carton, half of a two-carton span");
            ApplyRange(curtain, 0.0f, _curtainReading);
            Run(1);
            ExpectCount("curtain.height", FullScale, $"{mode}: the carton, the whole span");
            GD.Print($"  {mode,-8} curtain  carton {_curtainReading:0.###} m -> curtain.height = {Tags.Visible("curtain.height")}");

            if (mode == AnalogSignalMode.Ma4To20)
            {
                Expect(Tags.Force("curtain.wirebreak", true), "the curtain's wire break can be forced");
                Run(1);
                ExpectCount("curtain.height", OverflowCode, "4-20 mA: the curtain with its wire broken reads 7FFFh", 0);
                Tags.ClearForce("curtain.wirebreak");
                Run(1);
                ExpectCount("curtain.height", FullScale, "4-20 mA: the curtain reads the carton again once mended");
            }
            // Left at the two-carton span, so the round trip has a range that
            // is not the default to carry.
            ApplyRange(curtain, 0.0f, 2.0f * _curtainReading);
        }

        _curtainCarton?.QueueFree();
        _curtainCarton = null;
    }

    private void CurtainEmpty(AnalogSignalMode mode)
    {
        Expect(!Curtain.IsBlocked, $"{mode}: the curtain is clear once the carton has gone");
        Run(1);
        if (mode == AnalogSignalMode.Engineering)
            Expect(Convert.ToDouble(Tags.Visible("curtain.height")) == 0.0, "engineering: a clear curtain reads 0.0 m");
        else
            ExpectCount("curtain.height", 0, $"{mode}: a clear curtain reads 0", 0);
    }

    // ---------------------------------------------------------------- after the round trip

    private void CheckReloadedIp28()
    {
        CheckDeclaredIp28(AnalogSignalMode.Ma4To20, AnalogSignalMode.Ma4To20);

        foreach (var (id, _) in OutputParts)
        {
            var saved = PartProperties.Capture(Editor.NodeFor(id)!);
            Expect(saved.TryGetValue(AnalogOutput.SettingKey, out var key) && key == "ma_4_20",
                   $"{id}: the reloaded part carries {AnalogOutput.SettingKey}=ma_4_20 (got '{(saved.TryGetValue(AnalogOutput.SettingKey, out var k) ? k : "nothing")}')");
        }

        // By effect: counts written after the reload still mean what the saved
        // spans say they mean.
        Tags.Set("tank.drain", 0);
        Tags.Set("tank.fill", FullScale / 2);
        Tags.Set("gauge.value", FullScale / 2);
        Run(1);
        Expect(Math.Abs(TankPart.FillOpening - 50.0f) < 1e-3f,
               $"after reloading, 13824 opens the fill valve half way ({TankPart.FillOpening} %)");
        Expect(Math.Abs(Gauge.Value - 150.0f) < 1e-3f,
               $"after reloading, 13824 is half of the saved 0..300 plate ({Gauge.Value})");
        Tags.Set("tank.fill", 0);
        Tags.Set("gauge.value", 0);

        Expect(Math.Abs(Curtain.HeightSignal.RangeMax - 2.0f * _curtainReading) < 1e-6f,
               $"the curtain's span survives the round trip ({Curtain.HeightSignal.RangeMax} m, set to {2.0f * _curtainReading} m)");
        Expect(Math.Abs(Lift.HeightSignal.RangeMax - LiftSpan) < 1e-6f,
               $"the lift's span survives the round trip ({Lift.HeightSignal.RangeMax} m)");
        var panel = Panel;
        ExpectCount("panel.setpoint", ExpectedRaw(panel.Setpoint, panel.SetpointMin, panel.SetpointMax),
                    "after reloading, the pot's count is its setpoint on its plate");
    }
}
