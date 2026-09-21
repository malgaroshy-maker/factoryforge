using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Dynamically registers and manages tags for newly placed factory components in the TagBus.
/// </summary>
public static class PartTagManager
{
    private static readonly Dictionary<string, int> TypeCounters = new();

    public static void ResetCounters()
    {
        TypeCounters.Clear();
    }

    /// <summary>
    /// Tag suffix for each control on a <see cref="Parts.ButtonPanel"/>. One
    /// table rather than two literal lists, so registration and dispatch cannot
    /// drift apart and leave a button that presses nothing.
    /// </summary>
    public static string PanelTagSuffix(Parts.PanelButton which) => which switch
    {
        Parts.PanelButton.Start => "start",
        Parts.PanelButton.Stop => "stop",
        Parts.PanelButton.Reset => "reset",
        Parts.PanelButton.EmergencyStop => "estop",
        _ => which.ToString().ToLowerInvariant(),
    };

    /// <summary>Does the table already hold tags for this instance?</summary>
    public static bool HasTagsFor(string instanceId, TagTable tags)
    {
        string prefix = instanceId + ".";
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(prefix)) return true;
        }
        return false;
    }

    /// <summary>
    /// Remove every tag this manager registered for an instance. Ids are always
    /// "&lt;instanceId&gt;.&lt;suffix&gt;", so the prefix identifies them without
    /// needing to know which suffixes the part type used.
    /// </summary>
    public static void UnregisterPartTags(string instanceId, TagTable tags)
    {
        string prefix = instanceId + ".";
        var doomed = new List<string>();
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(prefix)) doomed.Add(tag.Id);
        }
        foreach (var id in doomed) tags.Remove(id);
    }

    /// <summary>
    /// Is this a usable instance id? Ids become tag prefixes, so a dot would
    /// make <c>a.b</c> + <c>.rotate</c> read as a three-level name nothing
    /// matches, and whitespace makes a mapping file miserable to hand-edit.
    /// </summary>
    public static bool IsValidInstanceId(string id) =>
        id.Length > 0
        && id.IndexOf('.') < 0
        && id.IndexOf(' ') < 0
        && !id.StartsWith("_");

    /// <summary>
    /// Move every tag under <paramref name="oldId"/> to <paramref name="newId"/>,
    /// keeping each one's type, kind and current value.
    ///
    /// Renaming matters because the auto-generated ids
    /// (<c>pushermechanism_2</c>) are what a PLC program has to be written
    /// against, and they are both unreadable and unstable — delete a part and
    /// re-place it and the number moves, silently breaking a mapping file that
    /// still points at the old one.
    ///
    /// Returns false and changes nothing if the target id is already taken, so a
    /// rejected rename cannot half-apply and leave a part driven by a mixture of
    /// two prefixes.
    /// </summary>
    /// <summary>
    /// Keep a type's auto-numbering ahead of an id that was chosen rather than
    /// minted — one that arrived in a scene file, or one a user typed into the
    /// rename box.
    ///
    /// The rename case is the one that was missing (HP-15), and it is worth
    /// saying why the part *key* added for HP-37 does not cover it: that key is
    /// the undo history's idea of identity and is deliberately private to the
    /// editor. An instance id is something else entirely — a tag prefix, shared
    /// with every driver and every PLC program written against the scene. Two
    /// parts may not share one, and nothing about a unique command key makes
    /// that true.
    /// </summary>
    public static void NoteInstanceId(string partType, string instanceId)
    {
        if (!TypeCounters.ContainsKey(partType)) TypeCounters[partType] = 0;

        int underscore = instanceId.LastIndexOf('_');
        if (underscore >= 0 && int.TryParse(instanceId[(underscore + 1)..], out int used))
            TypeCounters[partType] = System.Math.Max(TypeCounters[partType], used);
    }

    public static bool RenameInstance(string oldId, string newId, TagTable tags)
    {
        if (oldId == newId) return true;
        if (!IsValidInstanceId(newId)) return false;

        string oldPrefix = oldId + ".";
        string newPrefix = newId + ".";

        var moving = new List<Tag>();
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(oldPrefix)) moving.Add(tag);
        }

        // Check every destination before touching anything.
        foreach (var tag in moving)
        {
            if (tags.Contains(newPrefix + tag.Id[oldPrefix.Length..])) return false;
        }

        foreach (var tag in moving)
        {
            string suffix = tag.Id[oldPrefix.Length..];
            tags.Remove(tag.Id);
            tags.Add(new Tag(newPrefix + suffix, tag.Name, tag.Type, tag.Kind, tag.Value));
        }

        return true;
    }

    /// <summary>
    /// Register the tags a part type exposes.
    /// </summary>
    /// <param name="preferredId">Id to reuse instead of minting a fresh one —
    /// how a loaded scene keeps the wiring it was saved with. Without it, every
    /// load renamed the parts and left the reloaded scene driven by nothing.</param>
    /// <returns>The instance id, and whether this call actually created the tags.
    /// It did not when they already existed, which means the part is a view of
    /// tags something else owns (the default scene's belt and sensors), and the
    /// caller must not delete them with the part.</returns>
    public static (string Id, bool Owns) RegisterPartTags(Node3D partNode, string partType,
                                                          TagTable tags, string? preferredId = null)
    {
        if (!TypeCounters.ContainsKey(partType))
            TypeCounters[partType] = 0;

        string instanceId;
        if (preferredId is { Length: > 0 })
        {
            instanceId = preferredId;
            // Keep auto-numbering ahead of ids that arrived from a file, so a
            // part placed after a load cannot collide with one from it.
            NoteInstanceId(partType, preferredId);
        }
        else
        {
            // Step over anything already taken (HP-15).
            //
            // The counter alone is not enough and never was: it is advanced when
            // an id arrives from a file, and it was *not* advanced when a user
            // renamed a part. Rename conveyorbelt_1 to conveyorbelt_2, place a
            // new conveyor, and the counter still said 1 — so the new part minted
            // conveyorbelt_2, found tags already under that prefix, and adopted
            // them instead of creating its own. Two machines then answered one
            // PLC output, with nothing on screen to say which.
            //
            // Adoption is a real feature (the default scene's belt is a *view* of
            // tags SortingScene owns) but it is only ever right for an id the
            // caller asked for by name. An auto-minted id that collides is always
            // a bug, so this loop makes the collision impossible rather than
            // relying on every future caller to remember the counter.
            do
            {
                TypeCounters[partType]++;
                instanceId = $"{partType.ToLower()}_{TypeCounters[partType]}";
            }
            while (HasTagsFor(instanceId, tags));
        }

        // Tags already under this prefix belong to whoever created them first —
        // the simulation, for the default scene's belt and sensors. Adopt them
        // rather than re-adding (TagTable.Add throws on a duplicate id).
        if (HasTagsFor(instanceId, tags)) return (instanceId, false);

        int index = TypeCounters[partType];
        switch (partType)
        {
            case "ConveyorBelt":
                tags.Add(new Tag($"{instanceId}.rotate", $"Conveyor {index} (Rotate)", TagType.Bit, TagKind.Output));
                // The drive's own fault contact (FI-01). An Input, because
                // nothing in the simulation computes it -- it is raised by
                // whoever is playing maintenance, and read by the controller
                // exactly like a sensor.
                tags.Add(new Tag($"{instanceId}.fault", $"Conveyor {index} Drive Fault", TagType.Bit, TagKind.Input));
                break;

            case "RetroreflectiveSensor":
            case "InductiveSensor":
            case "PhotoelectricSensor":
                tags.Add(new Tag($"{instanceId}.detect", $"Sensor {index} (Detect)", TagType.Bit, TagKind.Input));
                break;

            case "PusherMechanism":
                tags.Add(new Tag($"{instanceId}.fault", $"Pusher {index} Drive Fault", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.extend", $"Pusher {index} (Extend)", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.extended", $"Pusher {index} (Extended)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.retracted", $"Pusher {index} (Retracted)", TagType.Bit, TagKind.Input));
                tags.Set($"{instanceId}.retracted", true);
                break;

            case "Emitter":
                tags.Add(new Tag($"{instanceId}.emit", $"Emitter {index} (Emit)", TagType.Bit, TagKind.Output));
                break;

            case "Remover":
                tags.Add(new Tag($"{instanceId}.count", $"Remover {index} (Count)", TagType.Int, TagKind.Input));
                break;

            case "ButtonPanel":
                tags.Add(new Tag($"{instanceId}.green", $"Panel {index} Green Lamp", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.red", $"Panel {index} Red Lamp", TagType.Bit, TagKind.Output));
                // The panel's buttons are Inputs: the operator drives them and
                // the controller reads them, exactly like a sensor.
                tags.Add(new Tag($"{instanceId}.start", $"Panel {index} Start (momentary)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.stop", $"Panel {index} Stop (momentary)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.reset", $"Panel {index} Reset (momentary)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.estop", $"Panel {index} E-Stop OK (NC)", TagType.Bit, TagKind.Input));
                // Normally closed, so a healthy circuit reads true and the scene
                // starts in the state a real panel powers up in.
                tags.Set($"{instanceId}.estop", true);
                // The setpoint pot, in the scene's own engineering units --
                // the template owns the range, not the controller (OP-01).
                tags.Add(new Tag($"{instanceId}.setpoint", $"Panel {index} Setpoint", TagType.Float, TagKind.Input));
                break;

            case "StackLight":
                tags.Add(new Tag($"{instanceId}.green", $"StackLight {index} Green", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.yellow", $"StackLight {index} Yellow", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.red", $"StackLight {index} Red", TagType.Bit, TagKind.Output));
                break;

            case "DigitalDisplay":
                tags.Add(new Tag($"{instanceId}.value", $"Display {index} Value", TagType.Int, TagKind.Output));
                break;

            case "LightArray":
                // A measurement, not a bit: how far up the curtain the tallest
                // blocked beam sits.
                tags.Add(new Tag($"{instanceId}.height", $"Light Array {index} Height (m)", TagType.Float, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.blocked", $"Light Array {index} Blocked", TagType.Bit, TagKind.Input));
                break;

            case "RollerConveyor":
                tags.Add(new Tag($"{instanceId}.rotate", $"Roller Conveyor {index} (Rotate)", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.fault", $"Roller Conveyor {index} Drive Fault", TagType.Bit, TagKind.Input));
                break;

            case "LevelTank":
                // The library's first analog part: valve openings and a level
                // transmitter, all in percent, all Float.
                tags.Add(new Tag($"{instanceId}.fill", $"Tank {index} Fill Valve (%)", TagType.Float, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.drain", $"Tank {index} Drain Valve (%)", TagType.Float, TagKind.Output));
                // A seized valve holds its opening (FI-01) -- the analog
                // failure, and a nastier one to diagnose than a stopped drive.
                tags.Add(new Tag($"{instanceId}.fault", $"Tank {index} Valve Fault", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.level", $"Tank {index} Level (%)", TagType.Float, TagKind.Input));
                break;

            case "WeighingConveyor":
                tags.Add(new Tag($"{instanceId}.rotate", $"WeighConveyor {index} Rotate", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.weight", $"WeighConveyor {index} Weight", TagType.Int, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"WeighConveyor {index} Drive Fault", TagType.Bit, TagKind.Input));
                break;

            case "VariableConveyor":
                // The library's first analog *drive* (CP-01). `speed` is what
                // the controller asks for and `actual` is what the drive has
                // managed so far; they are two tags because they are two
                // different numbers for as long as the ramp is running.
                tags.Add(new Tag($"{instanceId}.run", $"VFD Conveyor {index} Run", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.speed", $"VFD Conveyor {index} Speed Ref (%)", TagType.Float, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.actual", $"VFD Conveyor {index} Actual Speed (%)", TagType.Float, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"VFD Conveyor {index} Drive Fault", TagType.Bit, TagKind.Input));
                break;

            case "PivotDiverter":
                tags.Add(new Tag($"{instanceId}.divert", $"Diverter {index} (Divert)", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.diverted", $"Diverter {index} (Diverted)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.home", $"Diverter {index} (Home)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"Diverter {index} Drive Fault", TagType.Bit, TagKind.Input));
                // Parked, so the scene starts in the state the geometry shows.
                tags.Set($"{instanceId}.home", true);
                break;

            case "PickPlaceArm":
                tags.Add(new Tag($"{instanceId}.target", $"Gantry {index} Target (%)", TagType.Float, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.lower", $"Gantry {index} Lower", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.grip", $"Gantry {index} Vacuum", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.position", $"Gantry {index} Position (%)", TagType.Float, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.inposition", $"Gantry {index} In Position", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.lowered", $"Gantry {index} Lowered", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.raised", $"Gantry {index} Raised", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.holding", $"Gantry {index} Holding", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"Gantry {index} Drive Fault", TagType.Bit, TagKind.Input));
                tags.Set($"{instanceId}.raised", true);
                tags.Set($"{instanceId}.inposition", true);
                break;

            case "BarcodeScanner":
                tags.Add(new Tag($"{instanceId}.enable", $"Scanner {index} Enable", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.code", $"Scanner {index} Code", TagType.Int, TagKind.Input));
                // One scan wide, exactly like a panel button's pulse -- which
                // is why a program has to latch it rather than poll it.
                tags.Add(new Tag($"{instanceId}.read", $"Scanner {index} Read Pulse", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.present", $"Scanner {index} Item Present", TagType.Bit, TagKind.Input));
                // Armed on arrival: a scanner that has to be enabled before it
                // shows anything is a part that looks broken when it is placed.
                tags.Set($"{instanceId}.enable", true);
                break;

            case "AnalogGauge":
                tags.Add(new Tag($"{instanceId}.value", $"Gauge {index} Value", TagType.Float, TagKind.Output));
                break;

            case "AlarmBeacon":
                tags.Add(new Tag($"{instanceId}.beacon", $"Beacon {index} Light", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.horn", $"Beacon {index} Horn", TagType.Bit, TagKind.Output));
                break;

            case "HeatingStation":
                tags.Add(new Tag($"{instanceId}.heater", $"Heater {index} Power (%)", TagType.Float, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.temperature", $"Heater {index} Temperature (C)", TagType.Float, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.attemp", $"Heater {index} At Temperature", TagType.Bit, TagKind.Input));
                // A failed element still accepts and reports its command; only
                // the measurement gives it away (CP-07).
                tags.Add(new Tag($"{instanceId}.fault", $"Heater {index} Element Fault", TagType.Bit, TagKind.Input));
                break;

            case "SelectorSwitch":
                // An Int, not a set of mutually exclusive bits: one switch is
                // in exactly one position, and publishing three bits would
                // invite a program that handles two of them being true.
                tags.Add(new Tag($"{instanceId}.position", $"Selector {index} Position", TagType.Int, TagKind.Input));
                break;

            case "SafetyGate":
                // Normally closed, like the E-stop beside it: true while the
                // guard is shut, so a broken circuit reads as "not safe".
                tags.Add(new Tag($"{instanceId}.closed", $"Guard {index} Closed (NC)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.lock", $"Guard {index} Solenoid Lock", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.locked", $"Guard {index} Locked Shut", TagType.Bit, TagKind.Input));
                tags.Set($"{instanceId}.closed", true);
                break;

            case "StopGate":
                tags.Add(new Tag($"{instanceId}.raise", $"Stop {index} (Raise)", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.up", $"Stop {index} (Blade Up)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.down", $"Stop {index} (Blade Down)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"Stop {index} Drive Fault", TagType.Bit, TagKind.Input));
                // Parked, so the scene starts in the state the geometry shows.
                tags.Set($"{instanceId}.down", true);
                break;

            case "TurnTable":
                tags.Add(new Tag($"{instanceId}.index", $"Turntable {index} (Index)", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.athome", $"Turntable {index} (At Home)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.atindex", $"Turntable {index} (At Index)", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"Turntable {index} Drive Fault", TagType.Bit, TagKind.Input));
                tags.Set($"{instanceId}.athome", true);
                break;

            case "RotaryEncoder":
                // The count is an Int because that is what a high-speed counter
                // hands a program, and the rate is a Float because it is a
                // measurement -- the same split as the light curtain's
                // `blocked` and `height`.
                tags.Add(new Tag($"{instanceId}.count", $"Encoder {index} Count", TagType.Int, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.rate", $"Encoder {index} Rate (pulses/s)", TagType.Float, TagKind.Input));
                // A level, not an edge: holding the reset leg high holds the
                // count at zero, exactly like a counter's own reset.
                tags.Add(new Tag($"{instanceId}.reset", $"Encoder {index} Reset", TagType.Bit, TagKind.Output));
                break;

            case "CoolingFan":
                tags.Add(new Tag($"{instanceId}.run", $"Fan {index} Run", TagType.Bit, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.speed", $"Fan {index} Speed Ref (%)", TagType.Float, TagKind.Output));
                tags.Add(new Tag($"{instanceId}.airflow", $"Fan {index} Airflow (%)", TagType.Float, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.fault", $"Fan {index} Motor Fault", TagType.Bit, TagKind.Input));
                break;

            case "TwoHandControl":
                // All three are Inputs: the operator drives the buttons and the
                // *relay* decides the permissive. `valid` is an input to the
                // controller for the same reason a guard switch is -- the
                // program reads the safety device's verdict, it does not
                // compute it.
                tags.Add(new Tag($"{instanceId}.left", $"Two-Hand {index} Left Held", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.right", $"Two-Hand {index} Right Held", TagType.Bit, TagKind.Input));
                tags.Add(new Tag($"{instanceId}.valid", $"Two-Hand {index} Permissive", TagType.Bit, TagKind.Input));
                break;
        }

        return (instanceId, true);
    }
}
