using System.Collections.Generic;

namespace FactoryForge.Editor;

/// <summary>
/// One description of every part the palette offers (CP-20, CP-33).
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
/// are actually asking, which is "what will this give my PLC".
/// </summary>
public static class PartCatalog
{
    public sealed record PartInfo(
        string Type,
        string Label,
        string Group,
        string Summary,
        string Tags);

    /// <summary>Palette order. Groups are rendered in the order they first
    /// appear here, and parts within a group in the order listed.</summary>
    public static readonly IReadOnlyList<PartInfo> All = new PartInfo[]
    {
        new("ConveyorBelt", "Conveyor Belt", "TRANSPORT",
            "Surface-velocity belt with side rails and a drive-fault beacon. The workhorse.",
            "rotate · fault"),
        new("VariableConveyor", "VFD Conveyor", "TRANSPORT",
            "Belt behind a variable-frequency drive. The reference ramps, so commanded and actual speed genuinely disagree while it moves between them.",
            "run · speed · actual · fault"),
        new("RollerConveyor", "Roller Conveyor", "TRANSPORT",
            "Driven roller deck for pallets and totes that a belt would scuff.",
            "rotate · fault"),
        new("WeighingConveyor", "Weigh Conveyor", "TRANSPORT",
            "Belt section with a load cell under it, reading the carton's mass in grams.",
            "rotate · weight · fault"),
        new("TurnTable", "Transfer Turntable", "TRANSPORT",
            "Rotary index that turns a carton to a new lane. The load is held on by friction, so a deck told to index too fast throws it.",
            "index · athome · atindex · fault"),

        new("PhotoelectricSensor", "Photoelectric Sensor", "SENSORS",
            "Diffuse beam that reflects off the item itself. Cheapest, shortest range.",
            "detect"),
        new("RetroreflectiveSensor", "Retroreflective Sensor", "SENSORS",
            "Beams to a reflector across the lane, so it sees matt and dark items a diffuse sensor misses.",
            "detect"),
        new("InductiveSensor", "Inductive Sensor", "SENSORS",
            "Responds to metal only. Cardboard passes it as if the lane were empty.",
            "detect"),
        new("LightArray", "Light Curtain", "SENSORS",
            "Twelve beams reporting the height of the tallest blocked one — one part instead of a low/high sensor pair.",
            "height · blocked"),
        new("BarcodeScanner", "Barcode Scanner", "SENSORS",
            "Overhead reader. Reports what the item *is* as a code, with a one-scan read pulse a program has to latch.",
            "enable · code · read · present"),
        new("RotaryEncoder", "Measuring Encoder", "SENSORS",
            "Wheel riding the belt it is placed over, counting pulses per metre travelled — so product can be tracked by distance instead of by a timer.",
            "count · rate · reset"),

        new("PusherMechanism", "Pneumatic Pusher", "ACTUATORS",
            "Cylinder that strokes across the lane. A jam freezes it mid-stroke.",
            "extend · extended · retracted · fault"),
        new("PivotDiverter", "Pivot Diverter", "ACTUATORS",
            "Blade on a pivot that deflects a *moving* carton without stopping the line.",
            "divert · diverted · home · fault"),
        new("PickPlaceArm", "Pick & Place Gantry", "ACTUATORS",
            "Analog travel axis, vertical stroke and a vacuum cup that really picks a carton up. Three motions to sequence.",
            "target · lower · grip · position · inposition · lowered · raised · holding · fault"),
        new("StopGate", "Blade Stop", "ACTUATORS",
            "Blade that rises through the lane to hold cartons on a *running* belt. The only way to build an accumulation buffer here.",
            "raise · up · down · fault"),
        new("Chute", "Ramp (Chute)", "ACTUATORS",
            "30° gravity chute with guide rails. Incline and friction are a matched pair.",
            "— (static)"),

        new("Emitter", "Box Emitter", "PROCESS",
            "Feed gantry spawning tall and short cartons, optionally every Nth in metal.",
            "emit"),
        new("Remover", "Box Remover", "PROCESS",
            "Zone that despawns items and counts them. The count tag is pickable, so two removers can feed one total.",
            "count"),
        new("LevelTank", "Level Tank", "PROCESS",
            "Analog tank whose outflow follows Torricelli, so the process gain varies with level.",
            "fill · drain · level · fault"),
        new("HeatingStation", "Heating Station", "PROCESS",
            "First-order thermal plant with ambient loss. Asymmetric, so pure P control leaves a standing offset you can measure.",
            "heater · temperature · attemp · fault"),
        new("CoolingFan", "Cooling Fan", "PROCESS",
            "Ducted fan that pulls heat out of any heating station in reach — the second actuator a split-range loop needs.",
            "run · speed · airflow · fault"),

        new("ButtonPanel", "Control Panel", "OPERATOR",
            "Momentary Start/Stop/Reset, a maintained normally-closed E-stop, and a setpoint pot you drag.",
            "start · stop · reset · estop · setpoint · green · red"),
        new("StackLight", "Stack Light", "OPERATOR",
            "Three-stage tower light. Each lamp is separately clickable in Operate mode.",
            "green · yellow · red"),
        new("AlarmBeacon", "Alarm Beacon", "OPERATOR",
            "Rotating beacon that throws real light around, plus a horn with a visible diaphragm.",
            "beacon · horn"),
        new("DigitalDisplay", "Digital Display", "OPERATOR",
            "Seven-segment panel for an integer count, with a unit suffix.",
            "value"),
        new("AnalogGauge", "Analog Gauge", "OPERATOR",
            "Needle instrument for a float — a level, a speed, a temperature — read in the scene instead of the tag list.",
            "value"),
        new("SelectorSwitch", "Selector Switch", "OPERATOR",
            "A maintained rotary selector — Manual / Off / Auto. Stays where it is put, so the controller reads a position rather than an edge.",
            "position"),
        new("SafetyGate", "Guard Door", "SAFETY",
            "An interlocked guard. Its switch is closed while the door is shut, and a solenoid lock lets the controller decide whether it may be opened at all.",
            "closed · lock · locked"),
        new("TwoHandControl", "Two-Hand Control", "SAFETY",
            "Two palm buttons that only give a permissive when both are held *and* arrived together — so taping one down defeats nothing.",
            "left · right · valid"),
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
}
