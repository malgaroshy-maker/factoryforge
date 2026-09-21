using System;
using System.Collections.Generic;
using FactoryForge.Parts;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// One description of every part the palette offers (CP-20, CP-33), and since
/// HP-34 the only place in the editor that is allowed to name a part type at
/// all.
///
/// This exists because the same list was previously written out three times —
/// in the palette's own <c>_Ready</c>, in the save/load self-test's
/// <c>AllTypes</c>, and implicitly in <c>SceneEditor.CreatePartNode</c> — and a
/// part added to one and forgotten in another is exactly the kind of gap that
/// ships: a palette button that places nothing, or a new part nobody checks can
/// be saved. Anything that needs to know "what parts are there" asks here.
///
/// The description is not decoration either. Fifteen buttons labelled
/// "Light Array" and "Roller Conveyor" tell a newcomer nothing about which one
/// solves their problem, and the tag list is the answer to the question they
/// are actually asking, which is "what will this give my PLC". That tag list is
/// no longer written by hand: it is read off the part's own
/// <see cref="IPart.DeclareTags"/>, so a tooltip cannot promise I/O the part
/// does not have.
///
/// Adding a part is one new file under <c>engine/src/Parts/</c> and one entry
/// below. <c>--self-test=partcontract</c> fails if anything else in the editor
/// learns a part type's name.
/// </summary>
public static class PartCatalog
{
    /// <param name="Create">Builds the node, with whatever defaults make the
    /// part sensible the moment it lands from the palette. This is why the
    /// factory lives beside the description rather than in a second switch in
    /// the editor — the two could disagree, and a palette button that places
    /// nothing is the failure that produced.</param>
    public sealed record PartInfo(
        string Type,
        string Label,
        string Group,
        string Summary,
        Func<Node3D> Create)
    {
        /// <summary>The part's I/O, as a palette tooltip reads it. Derived from
        /// the part rather than restated here.</summary>
        public string Tags => TagSummary(Type);
    }

    private const float DeckThickness = 0.12f;
    private static readonly Vector3 StandardDeck = new(1.5f, DeckThickness, 0.5f);

    /// <summary>Palette order. Groups are rendered in the order they first
    /// appear here, and parts within a group in the order listed.</summary>
    public static readonly IReadOnlyList<PartInfo> All = new PartInfo[]
    {
        new("ConveyorBelt", "Conveyor Belt", "TRANSPORT",
            "Surface-velocity belt with side rails and a drive-fault beacon. The workhorse.",
            () => new ConveyorBelt { Size = StandardDeck }),
        new("VariableConveyor", "VFD Conveyor", "TRANSPORT",
            "Belt behind a variable-frequency drive. The reference ramps, so commanded and actual speed genuinely disagree while it moves between them.",
            () => new VariableConveyor { Size = StandardDeck }),
        new("RollerConveyor", "Roller Conveyor", "TRANSPORT",
            "Driven roller deck for pallets and totes that a belt would scuff.",
            () => new RollerConveyor { Size = StandardDeck }),
        new("WeighingConveyor", "Weigh Conveyor", "TRANSPORT",
            "Belt section with a load cell under it, reading the carton's mass in grams.",
            () => new WeighingConveyor { Size = StandardDeck }),
        new("TurnTable", "Transfer Turntable", "TRANSPORT",
            "Rotary index that turns a carton to a new lane. The load is held on by friction, so a deck told to index too fast throws it.",
            () => new TurnTable()),

        new("PhotoelectricSensor", "Photoelectric Sensor", "SENSORS",
            "Diffuse beam that reflects off the item itself. Cheapest, shortest range.",
            () => new PhotoelectricSensor { Range = 0.6f }),
        new("RetroreflectiveSensor", "Retroreflective Sensor", "SENSORS",
            "Beams to a reflector across the lane, so it sees matt and dark items a diffuse sensor misses.",
            () => new PhotoelectricSensor
            {
                Range = 0.75f, HeightAboveBelt = 0.08f, Mode = SensingMode.Retroreflective,
            }),
        new("InductiveSensor", "Inductive Sensor", "SENSORS",
            "Responds to metal only. Cardboard passes it as if the lane were empty.",
            () => new PhotoelectricSensor
            {
                Range = 0.75f, HeightAboveBelt = 0.06f, Mode = SensingMode.Inductive,
            }),
        new("LightArray", "Light Curtain", "SENSORS",
            "Twelve beams reporting the height of the tallest blocked one — one part instead of a low/high sensor pair.",
            () => new LightArray()),
        new("BarcodeScanner", "Barcode Scanner", "SENSORS",
            "Overhead reader. Reports what the item *is* as a code, with a one-scan read pulse a program has to latch.",
            () => new BarcodeScanner()),
        new("RotaryEncoder", "Measuring Encoder", "SENSORS",
            "Wheel riding the belt it is placed over, counting pulses per metre travelled — so product can be tracked by distance instead of by a timer.",
            () => new RotaryEncoder()),

        new("PneumaticCylinder", "Double-Acting Cylinder", "ACTUATORS",
            "Two coils on a 5/2 valve with no spring, and two reed switches with a gap between them. Mid-stroke, neither reed is made.",
            () => new PneumaticCylinder()),
        new("PusherMechanism", "Pneumatic Pusher", "ACTUATORS",
            "Cylinder that strokes across the lane. A jam freezes it mid-stroke.",
            () => new PusherMechanism { StrokeLength = 0.45f }),
        new("PivotDiverter", "Pivot Diverter", "ACTUATORS",
            "Blade on a pivot that deflects a *moving* carton without stopping the line.",
            () => new PivotDiverter()),
        new("PickPlaceArm", "Pick & Place Gantry", "ACTUATORS",
            "Analog travel axis, vertical stroke and a vacuum cup that really picks a carton up. Three motions to sequence.",
            () => new PickPlaceArm()),
        new("StopGate", "Blade Stop", "ACTUATORS",
            "Blade that rises through the lane to hold cartons on a *running* belt. The only way to build an accumulation buffer here.",
            () => new StopGate()),
        new("Chute", "Ramp (Chute)", "ACTUATORS",
            "30° gravity chute with guide rails. Incline and friction are a matched pair.",
            () => new Chute()),

        new("Emitter", "Box Emitter", "PROCESS",
            "Feed gantry spawning tall and short cartons, optionally every Nth in metal.",
            () => new Emitter()),
        new("Remover", "Box Remover", "PROCESS",
            "Zone that despawns items and counts them. The count tag is pickable, so two removers can feed one total.",
            () => new Remover()),
        new("LevelTank", "Level Tank", "PROCESS",
            "Analog tank whose outflow follows Torricelli, so the process gain varies with level.",
            () => new LevelTank()),
        new("HeatingStation", "Heating Station", "PROCESS",
            "First-order thermal plant with ambient loss. Asymmetric, so pure P control leaves a standing offset you can measure.",
            () => new HeatingStation()),
        new("CoolingFan", "Cooling Fan", "PROCESS",
            "Ducted fan that pulls heat out of any heating station in reach — the second actuator a split-range loop needs.",
            () => new CoolingFan()),

        new("DosingPump", "Dosing Pump", "PROCESS",
            "Variable-speed pump that really fills any tank in reach. Flow moves in a second where a level moves in a minute — the fast inner loop a cascade needs.",
            () => new DosingPump()),
        new("FlowMeter", "Flow Meter", "PROCESS",
            "In-line rate and a totaliser you can zero. Two instruments in one body: a process variable to control, and a counter to batch against.",
            () => new FlowMeter()),

        new("ButtonPanel", "Control Panel", "OPERATOR",
            "Momentary Start/Stop/Reset, a maintained normally-closed E-stop, and a setpoint pot you drag.",
            () => new ButtonPanel()),
        new("StackLight", "Stack Light", "OPERATOR",
            "Three-stage tower light. Each lamp is separately clickable in Operate mode.",
            () => new StackLight()),
        new("AlarmBeacon", "Alarm Beacon", "OPERATOR",
            "Rotating beacon that throws real light around, plus a horn with a visible diaphragm.",
            () => new AlarmBeacon()),
        new("DigitalDisplay", "Digital Display", "OPERATOR",
            "Seven-segment panel for an integer count, with a unit suffix.",
            () => new DigitalDisplay()),
        new("AnalogGauge", "Analog Gauge", "OPERATOR",
            "Needle instrument for a float — a level, a speed, a temperature — read in the scene instead of the tag list.",
            () => new AnalogGauge()),
        new("SelectorSwitch", "Selector Switch", "OPERATOR",
            "A maintained rotary selector — Manual / Off / Auto. Stays where it is put, so the controller reads a position rather than an edge.",
            () => new SelectorSwitch()),
        new("MotorStarter", "Motor Starter", "ELECTRICAL",
            "Contactor, auxiliary contact and an inverse-time thermal overload. The PLC drives the coil; the contactor drives the motor.",
            () => new MotorStarter()),

        new("SafetyGate", "Guard Door", "SAFETY",
            "An interlocked guard. Its switch is closed while the door is shut, and a solenoid lock lets the controller decide whether it may be opened at all.",
            () => new SafetyGate()),
        new("SafetyRelay", "Safety Relay", "SAFETY",
            "Dual-channel monitoring with a cross-check and an edge-triggered reset. It permits, it never commands — and it latches a fault on a channel that does not follow.",
            () => new SafetyRelay()),
        new("AreaScanner", "Area Scanner", "SAFETY",
            "Warning field, protective field, and muting that times out — so a mute taped on stops being honoured instead of becoming a permanent hole in the guard.",
            () => new AreaScanner()),
        new("TwoHandControl", "Two-Hand Control", "SAFETY",
            "Two palm buttons that only give a permissive when both are held *and* arrived together — so taping one down defeats nothing.",
            () => new TwoHandControl()),
    };

    /// <summary>Every part type, in palette order. The save/load self-test
    /// walks this rather than a copy of it, so a new part cannot ship without
    /// its round-trip being checked.</summary>
    public static string[] AllTypes()
    {
        var types = new string[All.Count];
        for (int i = 0; i < All.Count; i++) types[i] = All[i].Type;
        return types;
    }

    /// <summary>Is this a part type this build can actually build? Asked by the
    /// scene loader, so a file naming a part that does not exist here is
    /// reported rather than silently dropped (HP-02).</summary>
    public static bool IsKnownType(string partType) => Find(partType) is not null;

    public static PartInfo? Find(string partType)
    {
        foreach (var info in All)
        {
            if (info.Type == partType) return info;
        }
        return null;
    }

    /// <summary>Human label for a part type, falling back to the type name so a
    /// part missing from the catalog still shows something rather than an empty
    /// string.</summary>
    public static string LabelFor(string partType) => Find(partType)?.Label ?? partType;

    /// <summary>Build a part. Null for a type this build does not have.</summary>
    public static Node3D? Create(string partType) => Find(partType)?.Create();

    // ---------- what a type's I/O is, asked of the part itself

    private static readonly Dictionary<string, string[]> SuffixCache = new();

    /// <summary>
    /// The tag suffixes a part type owns, in declaration order.
    ///
    /// Answered by building one throwaway instance and asking it — which is why
    /// <see cref="IPart.DeclareTags"/> must not depend on anything
    /// <c>_Ready</c> builds. Cached, because the palette, the per-tick dispatch
    /// cache and the fault tool all ask, and the answer cannot change at
    /// runtime.
    ///
    /// This replaces <c>PlacedPart.TagSuffixesByType</c>, which was a hand-kept
    /// second copy of the registration switch and exactly the place per-part
    /// knowledge would pool next if HP-34 had only killed the switches.
    /// </summary>
    public static IReadOnlyList<string> TagSuffixes(string partType)
    {
        if (SuffixCache.TryGetValue(partType, out var cached)) return cached;

        string[] suffixes = Array.Empty<string>();
        if (Find(partType) is { } info)
        {
            var probe = info.Create();
            if (probe is IPart part)
            {
                var builder = new PartTagBuilder(null, partType, 1);
                part.DeclareTags(builder);
                suffixes = new string[builder.Suffixes.Count];
                for (int i = 0; i < suffixes.Length; i++) suffixes[i] = builder.Suffixes[i];
            }
            // Freed rather than queued: this node never entered the tree, and a
            // queue would hold it until a frame that may never come in a
            // headless probe.
            probe.Free();
        }

        SuffixCache[partType] = suffixes;
        return suffixes;
    }

    /// <summary>The palette tooltip's tag line. A part with no I/O says so
    /// rather than showing an empty field.</summary>
    public static string TagSummary(string partType)
    {
        var suffixes = TagSuffixes(partType);
        return suffixes.Count == 0 ? "— (static)" : string.Join(" · ", suffixes);
    }

    /// <summary>Does this part type have a drive that can be failed? Derived
    /// from the tag set rather than listed twice: anything that registered a
    /// <c>.fault</c> tag can be faulted, and anything that did not, cannot.
    /// </summary>
    public static bool CanFault(string partType)
    {
        foreach (string suffix in TagSuffixes(partType))
        {
            if (suffix == "fault") return true;
        }
        return false;
    }
}
