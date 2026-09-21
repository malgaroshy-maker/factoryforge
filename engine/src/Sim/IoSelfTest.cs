using System.Collections.Generic;
using System.Linq;
using FactoryForge.Editor;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check of the path from "scene you built" to "PLC you can wire".
///
/// <code>godot --headless --path engine -- --self-test=io</code>
///
/// Covers renaming (the ids a PLC program is written against) and the I/O
/// export (the file that carries those ids to the controller side). Both are
/// quiet failures by nature: a rename that half-applies leaves a part driven by
/// two prefixes, and an export that drops a tag produces an input that is false
/// forever, which looks exactly like a sensor that never triggers.
/// </summary>
public partial class IoSelfTest : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    private readonly List<string> _failures = new();
    private int _step;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private bool _done;

    public override void _PhysicsProcess(double delta)
    {
        if (++_step < 3 || _done) return;   // let the scene finish building
        // Quit() takes effect at the end of the frame, so without this latch the
        // whole body runs again on the next tick — against a scene the first run
        // has already renamed, burying the real failure under repeats of itself.
        _done = true;

        if (Editor is null)
        {
            GD.PrintErr("self-test io: no editor");
            GetTree().Quit(1);
            return;
        }

        // A throw here used to escape into Godot's handler, which logs it and
        // carries on — so the run ended via --duration with exit code 0 and a
        // crashed test read as a pass. Anything thrown is a failure.
        try
        {
            CheckRename();
            CheckRenameKeepsForces();
            CheckExport();
        }
        catch (System.Exception ex)
        {
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        if (_failures.Count == 0)
        {
            GD.Print("self-test io: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test io: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private void CheckRename()
    {
        // The panel is the one default part that owns its tags, so it is the one
        // that can legitimately be renamed.
        Expect(Tags.Contains("panel.start"), "panel.start exists before the rename");

        // Give it a value first: a rename must carry values across, or a latched
        // E-stop would silently clear itself the moment someone renamed a part.
        Tags.Set("panel.estop", false);

        Expect(Editor!.TryRenamePart("panel", "operator_station", out string problem),
               $"rename panel -> operator_station succeeds ({problem})");

        Expect(Tags.Contains("operator_station.start"), "tags moved to the new prefix");
        Expect(!Tags.Contains("panel.start"), "no tag is left behind on the old prefix");
        Expect(Tags.Contains("operator_station.estop")
               && (bool)Tags.Visible("operator_station.estop") == false,
               "a tag's value survives the rename");

        var moved = Tags.Get("operator_station.start");
        Expect(moved is not null && moved.Kind == TagKind.Input && moved.Type == TagType.Bit,
               "the moved tag keeps its type and direction");

        Expect(Editor.PlacedPartIds().Contains("operator_station"),
               "the editor knows the part by its new id");

        // Rules that must bite.
        Expect(!Editor.TryRenamePart("operator_station", "has.dot", out _),
               "an id containing a dot is rejected (it would break the prefix split)");
        Expect(!Editor.TryRenamePart("operator_station", "has space", out _),
               "an id containing a space is rejected");
        Expect(!Editor.TryRenamePart("operator_station", "", out _),
               "an empty id is rejected");
        Expect(!Editor.TryRenamePart("operator_station", "conveyor", out _),
               "renaming onto an id already in use is rejected");
        Expect(Tags.Contains("operator_station.start"),
               "a rejected rename leaves the tags exactly where they were");

        // The default belt is a view of tags SortingScene owns by name.
        Expect(!Editor.TryRenamePart("conveyor", "belt_one", out _),
               "a part that only mirrors simulation-owned tags cannot be renamed");
        Expect(Tags.Contains("conveyor.rotate"), "the refused rename left conveyor.rotate alone");

        // The tag layer keeps its own promise. The editor rejects a target whose
        // prefix is in use at all, which is the stricter and correct rule and
        // means the check inside RenameInstance is never reached from the UI —
        // so test it directly, or an all-or-nothing guarantee stated in its doc
        // comment is guarded by nothing.
        Tags.Add(new Tag("spare.start", "Spare", TagType.Bit, TagKind.Input));
        Expect(!PartTagManager.RenameInstance("operator_station", "spare", Tags),
               "RenameInstance refuses a target whose tag id already exists");
        Expect(Tags.Contains("operator_station.start") && Tags.Contains("operator_station.estop"),
               "a refused RenameInstance moved nothing at all");
        Tags.Remove("spare.start");

        Expect(Editor.TryRenamePart("operator_station", "panel", out _), "rename back");
    }

    /// <summary>
    /// HP-17. A force has to move with the tag it is pinned to.
    ///
    /// Renaming removes and rebuilds every tag under the prefix, and
    /// TagTable.Remove drops the force with it — which is right for a part being
    /// deleted and wrong here. So a rename quietly released every force on the
    /// part. The unsafe direction is the point: force a motor off while the PLC
    /// is commanding it on, rename the part, and the rename *starts the motor*.
    /// The safest control in the inspector, undone by an action that sounds like
    /// paperwork, with nothing to say it happened.
    /// </summary>
    private void CheckRenameKeepsForces()
    {
        // panel.estop is normally closed -- true is healthy -- so forcing it
        // false is the safe state a person pins while they work on something.
        // The underlying value is left true, so a released force is visible as
        // the value springing back rather than as nothing at all.
        Tags.Set("panel.estop", true);
        Tags.Force("panel.estop", false);
        Expect(Tags.IsForced("panel.estop"), "the E-stop is forced before the rename");
        Expect((bool)Tags.Visible("panel.estop") == false, "and reads as struck");

        Expect(Editor!.TryRenamePart("panel", "operator_station", out string problem),
               $"the forced part can be renamed ({problem})");

        Expect(Tags.IsForced("operator_station.estop"),
               "the force moved to the new tag id rather than being released");
        Expect(Tags.Contains("operator_station.estop")
               && (bool)Tags.Visible("operator_station.estop") == false,
               "and still reads as struck, not as healthy again");

        // Releasing it still works afterwards, or "forced" could be a flag
        // nothing can clear.
        Tags.ClearForce("operator_station.estop");
        Expect(!Tags.IsForced("operator_station.estop"), "and can still be released");
        Expect((bool)Tags.Visible("operator_station.estop"),
               "revealing the underlying value the force was hiding");

        Expect(Editor.TryRenamePart("operator_station", "panel", out _), "rename back");
    }

    private void CheckExport()
    {
        const string mapPath = "user://selftest_io_mapping.json";
        const string csvPath = "user://selftest_io_tags.csv";

        var existing = new Dictionary<string, string> { ["panel.start"] = "ns=3;s=\"FF\".\"Start\"" };
        string written = IoExport.WriteMappingFile(Tags, mapPath, existing);
        Expect(written.Length > 0, "the mapping file is written");

        using (var file = Godot.FileAccess.Open(mapPath, Godot.FileAccess.ModeFlags.Read))
        {
            string json = file?.GetAsText() ?? "";
            var parsed = Json.ParseString(json).Obj as Godot.Collections.Dictionary;
            Expect(parsed is not null, "the mapping file is valid JSON");

            if (parsed is not null)
            {
                int missing = 0;
                foreach (var tag in Tags)
                {
                    if (!parsed.ContainsKey(tag.Id)) missing++;
                }
                Expect(missing == 0, $"every tag appears in the mapping file ({missing} missing)");

                // Re-exporting must not wipe addresses somebody typed in.
                Expect(parsed.ContainsKey("panel.start")
                       && parsed["panel.start"].AsString() == existing["panel.start"],
                       "an existing address is preserved on re-export");

                Expect(parsed.ContainsKey("_usage"),
                       "the file carries the command that consumes it");
            }
        }

        Expect(IoExport.WriteTagCsv(Tags, csvPath).Length > 0, "the tag CSV is written");
        using (var file = Godot.FileAccess.Open(csvPath, Godot.FileAccess.ModeFlags.Read))
        {
            string csv = file?.GetAsText() ?? "";
            // Header plus one line per tag, and a trailing newline.
            int lines = csv.TrimEnd('\n').Split('\n').Length;
            Expect(lines == Tags.Count + 1,
                   $"the CSV has a row per tag (got {lines}, want {Tags.Count + 1})");
        }

        // Suggested addresses must be unique, or two tags share a bit and the
        // symbol table built from this is wrong in a way nothing announces.
        var suggested = IoExport.SuggestIecAddresses(Tags);
        Expect(suggested.Count == Tags.Count, "every tag gets a suggested address");

        var seen = new HashSet<string>();
        int collisions = 0;
        foreach (var address in suggested.Values)
        {
            if (!seen.Add(address)) collisions++;
        }
        Expect(collisions == 0, $"suggested addresses are unique ({collisions} collisions)");

        foreach (var tag in Tags)
        {
            // Inputs are read by the PLC (%I), outputs written by it (%Q).
            string want = tag.Kind == TagKind.Input ? "%I" : "%Q";
            if (!suggested[tag.Id].StartsWith(want))
            {
                Expect(false, $"{tag.Id} ({tag.Kind}) should map to {want}, got {suggested[tag.Id]}");
                break;
            }
        }
    }
}
