using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using FactoryForge.Editor;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that a scene survives being written to disk and read back,
/// for every part type in the palette.
///
/// <code>godot --headless --path engine -- --self-test=scene</code>
///
/// Save/load is the feature with the widest blast radius and the quietest
/// failures: a part whose settings are not captured comes back at its defaults,
/// which looks like a scene that was never tuned rather than a bug. It has gone
/// wrong twice before — removers lost the tag they count into, sensors lost the
/// flag saying the simulation owns their tag.
///
/// Every part type is exercised, not a representative sample, because the cost
/// of forgetting one is exactly the silent kind.
/// </summary>
public partial class SceneSelfTest : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    /// <summary>Every type the palette offers. Read from the catalog rather
    /// than copied into a literal here: the copy is what let a part ship in the
    /// palette with nobody checking it could be saved, which is precisely the
    /// silent failure this test exists for (CP-33).</summary>
    private static string[] AllTypes => PartCatalog.AllTypes();

    private const string ScenePath = "user://selftest_scene.json";

    private readonly List<string> _failures = new();
    private int _step;
    private bool _done;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _PhysicsProcess(double delta)
    {
        // Phase 1 builds and saves; phase 2 loads and inspects. They cannot be
        // the same tick: parts build their geometry in _Ready, which does not
        // run until the node has been in the tree for a frame.
        if (++_step == 3) { BuildAndSave(); return; }
        if (_step != 8 || _done) return;
        _done = true;

        try
        {
            CheckRoundTrip();
            CheckDispatchSurvives();
            CheckClearUndo();
            CheckRotateAndDuplicate();
            CheckASaveThatCannotLandSaysSo();
            CheckAFailedSaveLeavesTheLastGoodFile();
        }
        catch (System.Exception ex)
        {
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        if (_failures.Count == 0)
        {
            GD.Print("self-test scene: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test scene: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    /// <summary>
    /// Write a scene containing one of everything, with non-default settings, by
    /// hand. Hand-written rather than saved from a built scene so the test also
    /// covers the case that actually happens to users: a file produced by
    /// something other than this exact build.
    /// </summary>
    private void BuildAndSave()
    {
        var data = new SceneData { Name = "selftest-every-part" };

        for (int i = 0; i < AllTypes.Length; i++)
        {
            var props = new Dictionary<string, string>();

            // Values deliberately unlike the defaults, so "it came back right"
            // cannot be satisfied by a part that ignored the file entirely.
            switch (AllTypes[i])
            {
                case "ConveyorBelt":
                case "RollerConveyor":
                case "WeighingConveyor":
                    props["speed"] = "0.77";
                    props["friction"] = "0.66";
                    break;
                case "PhotoelectricSensor":
                case "RetroreflectiveSensor":
                case "InductiveSensor":
                    props["range"] = "0.93";
                    props["height"] = "0.17";
                    props["mode"] = "Inductive";
                    props["visual_only"] = "true";
                    break;
                case "PusherMechanism":
                    props["stroke"] = "0.61";
                    props["speed"] = "1.9";
                    props["visual_only"] = "true";
                    break;
                case "Chute":
                    props["incline"] = "37";
                    props["friction"] = "0.11";
                    break;
                case "LevelTank":
                    props["fill_rate"] = "22";
                    props["drain_rate"] = "13";
                    break;
                case "LightArray":
                    props["beams"] = "9";
                    props["curtain_height"] = "0.55";
                    break;
                case "Emitter":
                    props["metal_every"] = "4";
                    break;
                case "Remover":
                    props["count_tag"] = "counter.tall";
                    break;
                case "ButtonPanel":
                    // The pot's scale plate and where the knob was left. Saving
                    // the live value rather than a separate "default" is
                    // deliberate (see PartProperties): a real pot does not
                    // spring back, and a scene reopened mid-tuning should open
                    // where you left it.
                    props["setpoint_min"] = "0";
                    props["setpoint_max"] = "450";
                    props["setpoint_unit"] = "kPa";
                    props["setpoint"] = "175";
                    break;
                case "DigitalDisplay":
                    props["unit"] = "kg";
                    break;
                case "VariableConveyor":
                    // No "speed": a VFD belt's Speed is recomputed by the drive
                    // every tick and is deliberately not captured (see
                    // PartProperties). Friction still is, and so are the two
                    // settings that make this a drive rather than a belt --
                    // a subclass whose own settings were dropped would still
                    // look fine on the inherited ones, which is exactly the
                    // kind of half-saved part this test exists to catch.
                    props["friction"] = "0.66";
                    props["max_speed"] = "1.35";
                    props["accel_rate"] = "17";
                    break;
                case "PivotDiverter":
                    props["divert_angle"] = "38";
                    props["swing_speed"] = "155";
                    props["blade_length"] = "0.51";
                    break;
                case "PickPlaceArm":
                    props["rail_length"] = "2.1";
                    props["travel_speed"] = "42";
                    props["stroke"] = "0.48";
                    props["tolerance"] = "2.5";
                    break;
                case "BarcodeScanner":
                    props["height"] = "0.37";
                    props["window"] = "0.31";
                    break;
                case "AnalogGauge":
                    props["scale_min"] = "-20";
                    props["scale_max"] = "260";
                    props["alarm_at"] = "210";
                    props["unit"] = "degC";
                    break;
                case "AlarmBeacon":
                    props["rotation_speed"] = "2.4";
                    break;
                case "HeatingStation":
                    props["heater_power"] = "70";
                    props["thermal_mass"] = "18";
                    props["target_temp"] = "155";
                    break;
                case "SelectorSwitch":
                    props["positions"] = "4";
                    props["labels"] = "OFF,SLOW,FAST,PURGE";
                    props["detent"] = "2";
                    break;
                case "SafetyGate":
                    props["travel"] = "0.9";
                    props["slide_speed"] = "1.7";
                    break;
            }

            data.Parts.Add(new PartInstanceData
            {
                Id = $"probe_{AllTypes[i].ToLowerInvariant()}",
                Type = AllTypes[i],
                // Spread along X so nothing overlaps; on the work plane, as the
                // mounting convention requires.
                Position = new[] { -6.0f + i * 1.0f, 0.5f, -4.0f },
                Rotation = new[] { 0.0f, i % 2 == 0 ? 0.0f : Mathf.Pi / 2.0f, 0.0f },
                Properties = props,
            });
        }

        // A key no build knows: loading must ignore it, not fail. SceneData's
        // docs promise a scene from a newer build still opens.
        data.Parts[0].Properties!["unknown_future_key"] = "42";

        using (var file = Godot.FileAccess.Open(ScenePath, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }

        Editor!.LoadSceneFromFile(ScenePath);
    }

    private void CheckRoundTrip()
    {
        var ids = Editor!.PlacedPartIds();

        Expect(Editor.SceneName == "selftest-every-part",
               $"the scene adopts the name in the file (got '{Editor.SceneName}')");

        foreach (string type in AllTypes)
        {
            string id = $"probe_{type.ToLowerInvariant()}";
            Expect(ids.Contains(id), $"{type} was loaded");

            // Chute is the one part with no I/O of its own, by design.
            if (type == "Chute") continue;
            Expect(PartTagManager.HasTagsFor(id, Tags), $"{type} registered its tags");
        }

        // Save it back out and compare the properties we set. This is the check
        // that catches a setting missing from PartProperties: it loads fine and
        // then quietly saves the default.
        const string resaved = "user://selftest_scene_resaved.json";
        Editor.SaveSceneToFile(resaved);

        using var file = Godot.FileAccess.Open(resaved, Godot.FileAccess.ModeFlags.Read);
        var data = SceneData.FromJson(file?.GetAsText() ?? "");
        Expect(data is not null, "the re-saved scene is readable");
        if (data is null) return;

        Expect(data.Parts.Count == AllTypes.Length,
               $"every part was saved again ({data.Parts.Count} of {AllTypes.Length})");

        foreach (var part in data.Parts)
        {
            var props = part.Properties;
            if (props is null) continue;

            switch (part.Type)
            {
                case "ConveyorBelt":
                case "RollerConveyor":
                case "WeighingConveyor":
                    ExpectNear(props, "speed", 0.77f, part.Type);
                    ExpectNear(props, "friction", 0.66f, part.Type);
                    break;
                case "PhotoelectricSensor":
                case "RetroreflectiveSensor":
                case "InductiveSensor":
                    ExpectNear(props, "range", 0.93f, part.Type);
                    ExpectNear(props, "height", 0.17f, part.Type);
                    Expect(props.GetValueOrDefault("mode") == "Inductive",
                           $"{part.Type} kept its sensing mode");
                    Expect(props.GetValueOrDefault("visual_only") == "true",
                           $"{part.Type} kept visual_only (the flag that stops two authors fighting over one tag)");
                    break;
                case "PusherMechanism":
                    ExpectNear(props, "stroke", 0.61f, part.Type);
                    Expect(props.GetValueOrDefault("visual_only") == "true",
                           $"{part.Type} kept visual_only");
                    break;
                case "Chute":
                    ExpectNear(props, "incline", 37.0f, part.Type);
                    ExpectNear(props, "friction", 0.11f, part.Type);
                    break;
                case "LevelTank":
                    ExpectNear(props, "fill_rate", 22.0f, part.Type);
                    ExpectNear(props, "drain_rate", 13.0f, part.Type);
                    break;
                case "LightArray":
                    ExpectNear(props, "beams", 9.0f, part.Type);
                    ExpectNear(props, "curtain_height", 0.55f, part.Type);
                    break;
                case "Emitter":
                    ExpectNear(props, "metal_every", 4.0f, part.Type);
                    break;
                case "Remover":
                    Expect(props.GetValueOrDefault("count_tag") == "counter.tall",
                           "Remover kept the tag it counts into");
                    break;
                case "DigitalDisplay":
                    Expect(props.GetValueOrDefault("unit") == "kg", "DigitalDisplay kept its unit");
                    break;
                case "ButtonPanel":
                    ExpectNear(props, "setpoint_max", 450.0f, part.Type);
                    ExpectNear(props, "setpoint", 175.0f, part.Type);
                    Expect(props.GetValueOrDefault("setpoint_unit") == "kPa",
                           "ButtonPanel kept the unit its scale plate is graduated in");
                    break;
                case "VariableConveyor":
                    Expect(!props.ContainsKey("speed"),
                           "VariableConveyor does not save the speed its drive computes");
                    ExpectNear(props, "friction", 0.66f, part.Type);
                    ExpectNear(props, "max_speed", 1.35f, part.Type);
                    ExpectNear(props, "accel_rate", 17.0f, part.Type);
                    break;
                case "PivotDiverter":
                    ExpectNear(props, "divert_angle", 38.0f, part.Type);
                    ExpectNear(props, "swing_speed", 155.0f, part.Type);
                    ExpectNear(props, "blade_length", 0.51f, part.Type);
                    break;
                case "PickPlaceArm":
                    ExpectNear(props, "rail_length", 2.1f, part.Type);
                    ExpectNear(props, "travel_speed", 42.0f, part.Type);
                    ExpectNear(props, "stroke", 0.48f, part.Type);
                    break;
                case "BarcodeScanner":
                    ExpectNear(props, "height", 0.37f, part.Type);
                    ExpectNear(props, "window", 0.31f, part.Type);
                    break;
                case "AnalogGauge":
                    ExpectNear(props, "scale_max", 260.0f, part.Type);
                    ExpectNear(props, "alarm_at", 210.0f, part.Type);
                    Expect(props.GetValueOrDefault("unit") == "degC",
                           "AnalogGauge kept the unit on its scale plate");
                    break;
                case "AlarmBeacon":
                    ExpectNear(props, "rotation_speed", 2.4f, part.Type);
                    break;
                case "HeatingStation":
                    ExpectNear(props, "heater_power", 70.0f, part.Type);
                    ExpectNear(props, "thermal_mass", 18.0f, part.Type);
                    ExpectNear(props, "target_temp", 155.0f, part.Type);
                    break;
                case "SelectorSwitch":
                    ExpectNear(props, "positions", 4.0f, part.Type);
                    // The detent the switch was left in, not a default: a real
                    // selector does not spring back, and a scene reopened
                    // mid-experiment should reopen in the mode it was in.
                    ExpectNear(props, "detent", 2.0f, part.Type);
                    Expect(props.GetValueOrDefault("labels") == "OFF,SLOW,FAST,PURGE",
                           "SelectorSwitch kept the labels on its escutcheon");
                    break;
                case "SafetyGate":
                    ExpectNear(props, "travel", 0.9f, part.Type);
                    ExpectNear(props, "slide_speed", 1.7f, part.Type);
                    break;
            }
        }
    }

    /// <summary>
    /// The loaded parts have to survive being driven. Every output tag is set
    /// high at once — not a realistic scene state, deliberately: it reaches
    /// every branch of the part dispatch in one tick, which is where a null
    /// reference in a rarely-used part would hide.
    /// </summary>
    private void CheckDispatchSurvives()
    {
        foreach (var tag in Tags)
        {
            if (tag.Kind != TagKind.Output) continue;
            Tags.Set(tag.Id, tag.Type switch
            {
                TagType.Bit => true,
                TagType.Int => 1,
                _ => (object)50.0,
            });
        }

        // If the dispatch throws, the catch in _PhysicsProcess records it.
        Editor!._PhysicsProcess(1.0 / 60.0);
        Editor._PhysicsProcess(1.0 / 60.0);
        Expect(true, "part dispatch survives every output being driven");
    }

    /// <summary>
    /// Clear is the most destructive action in the editor (FF-01): it must be
    /// undoable, and undoing it must bring back every part with its tags and
    /// wiring intact, not a fresh scene that merely looks similar.
    /// </summary>
    private void CheckClearUndo()
    {
        var before = Editor!.PlacedPartIds().OrderBy(id => id).ToList();
        Expect(before.Count == AllTypes.Length, "scene has parts to clear before the check runs");

        Editor.ClearAllPlacedPartsWithUndo();
        Expect(Editor.PlacedPartIds().Count == 0, "Clear removes every part");

        Editor.Undo();
        var after = Editor.PlacedPartIds().OrderBy(id => id).ToList();
        Expect(after.SequenceEqual(before), $"Undo restores every part (got {after.Count} of {before.Count})");

        foreach (string type in AllTypes)
        {
            if (type == "Chute") continue;   // no I/O of its own, by design
            string id = $"probe_{type.ToLowerInvariant()}";
            Expect(PartTagManager.HasTagsFor(id, Tags), $"{type} kept its tags after undoing Clear");
        }
    }

    /// <summary>
    /// FF-20 (rotate a selected part, not just while placing) and FF-21
    /// (Ctrl+D duplicate). Both must be undoable — rotate back to exactly the
    /// original angle, duplicate removing exactly the one part it added.
    /// </summary>
    private void CheckRotateAndDuplicate()
    {
        const string probeId = "probe_conveyorbelt";

        Expect(Editor!.SelectPartForInspection(probeId), "a probe part is selectable to rotate/duplicate");
        float before = Editor.RotationYOf(probeId) ?? 0f;

        Editor.RotateSelectedPart();
        float rotated = Editor.RotationYOf(probeId) ?? 0f;
        Expect(!Mathf.IsEqualApprox(rotated, before), "rotating a selected part changes its Y rotation");
        Expect(Mathf.IsEqualApprox(rotated, before + Mathf.Pi / 2f), "rotate turns exactly 90 degrees");

        Editor.Undo();
        Expect(Mathf.IsEqualApprox(Editor.RotationYOf(probeId) ?? -999f, before),
               "undoing a rotate restores the original angle");

        var beforeDuplicate = Editor.PlacedPartIds().ToHashSet();
        Editor.SelectPartForInspection(probeId);
        Editor.DuplicateSelectedPart();
        var afterDuplicate = Editor.PlacedPartIds().ToHashSet();
        Expect(afterDuplicate.Count == beforeDuplicate.Count + 1,
               $"duplicate adds exactly one part (before {beforeDuplicate.Count}, after {afterDuplicate.Count})");

        Editor.Undo();
        Expect(Editor.PlacedPartIds().ToHashSet().SetEquals(beforeDuplicate),
               "undoing a duplicate removes exactly the part it added");
    }

    /// <summary>
    /// HP-01. A save that cannot land has to say so, and has to leave the title
    /// bar saying there are unsaved changes.
    ///
    /// The old code was <c>file?.StoreString(json)</c> followed unconditionally
    /// by <c>IsDirty = false</c> and "Saved scene to …", so a path that could
    /// not be opened at all reported success and cleared the one indicator a
    /// person has that their work is still only in memory.
    ///
    /// The unwritable path here is a file *inside* a file: `user://` exists, the
    /// scene file in it exists, and nothing can be created underneath it on any
    /// filesystem. No permissions to set up, no platform-specific read-only
    /// directory, and it fails at the open rather than part-way through.
    /// </summary>
    private void CheckASaveThatCannotLandSaysSo()
    {
        Editor!.MarkDirty();
        Expect(Editor.IsDirty, "the scene starts this check with unsaved changes");

        string impossible = $"{ScenePath}/not_a_directory/scene.json";
        bool reported = Editor.SaveSceneToFile(impossible);

        Expect(!reported, "a save to a path that cannot be opened reports failure");
        Expect(Editor.IsDirty,
               "and leaves the scene marked unsaved, so the title bar still says so");
        Expect(!Godot.FileAccess.FileExists(impossible),
               "and wrote nothing");

        // The ordinary path still works, or the check above would pass for a
        // save that had simply stopped working.
        Expect(Editor.SaveSceneToFile("user://selftest_scene_ok.json"),
               "a save to a writable path still reports success");
        Expect(!Editor.IsDirty, "and clears the unsaved marker");
    }

    /// <summary>
    /// HP-47. A save that does not complete must leave the file that was
    /// already there.
    ///
    /// Opening a file for writing truncates it, so the old code destroyed the
    /// last good scene before writing a byte of the new one. Any interruption
    /// after that point — a crash, a full disk, a drive pulled out — left a
    /// truncated file where a working scene used to be, and for most people
    /// that file is the only copy.
    ///
    /// The interruption is arranged by putting a *directory* where the
    /// half-written file wants to go. It is a stand-in for "the write did not
    /// complete", and what it proves is the ordering: the destination is not
    /// opened for writing at all until a finished file exists beside it.
    /// </summary>
    private void CheckAFailedSaveLeavesTheLastGoodFile()
    {
        const string target = "user://selftest_scene_atomic.json";
        const string partial = target + ".part";

        Expect(Editor!.SaveSceneToFile(target), "the first save lands");
        Expect(!Godot.FileAccess.FileExists(partial),
               "and leaves no half-written file beside the real one");

        string good = ReadAll(target);
        Expect(good.Length > 0, "the saved scene has content to compare against");

        // Make the scene genuinely different, so "the old file survived" cannot
        // be satisfied by a save that wrote the same bytes back.
        int wasCount = Editor.PlacedPartIds().Count;
        Editor.SelectPartByIndex(0);
        Editor.DeleteSelectedPart();
        Expect(Editor.PlacedPartIds().Count == wasCount - 1,
               "the scene changed between the two saves");

        // Block the half-written file's path with a directory nothing can open
        // as a file.
        Expect(Godot.DirAccess.MakeDirAbsolute(partial) == Error.Ok,
               "the interruption can be arranged");

        bool landed = Editor.SaveSceneToFile(target);
        Expect(!landed, "a save that cannot complete reports failure");
        Expect(Editor.IsDirty, "and leaves the scene marked unsaved");

        string after = ReadAll(target);
        Expect(after == good,
               $"and the scene file that was already there is untouched "
               + $"({after.Length} characters, was {good.Length})");
        Expect(SceneData.FromJson(after) is not null,
               "so the last good scene is still loadable");

        // Clear the blockage and save again: replacing a file that already
        // exists is the ordinary case, and a rename-into-place that only worked
        // onto an empty slot would break every save after the first.
        Godot.DirAccess.RemoveAbsolute(partial);

        Expect(Editor.SaveSceneToFile(target), "and a save over the existing file still lands");
        string replaced = ReadAll(target);
        Expect(replaced != good, "replacing it actually changed the file");
        Expect(SceneData.FromJson(replaced)?.Parts.Count == wasCount - 1,
               "with the scene as it now stands");
        Expect(!Godot.FileAccess.FileExists(partial), "and no half-written file left over");
    }

    private static string ReadAll(string path)
    {
        using var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Read);
        return file?.GetAsText() ?? "";
    }

    private void ExpectNear(IDictionary<string, string> props, string key, float want, string type)
    {
        if (!props.TryGetValue(key, out string? raw)
            || !float.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out float got))
        {
            Expect(false, $"{type}.{key} is missing from the saved scene");
            return;
        }
        Expect(Mathf.Abs(got - want) < 0.01f, $"{type}.{key} round-tripped (want {want}, got {got})");
    }
}
