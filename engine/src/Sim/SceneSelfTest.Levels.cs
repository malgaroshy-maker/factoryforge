using System.Linq;
using FactoryForge.Editor;
using FactoryForge.Parts;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Levels (IP-15): a part on a raised work plane survives the file, the file
/// says it needs this build, an older build refuses it, and the editor places,
/// lifts and pastes on levels without flattening anything.
///
/// Every check here was watched failing with the thing it guards broken
/// (gotcha 24; the commands and what failed are in IP-15's commit): the spawn
/// that ignores the level, the capture that stops writing it, and the version
/// stamp that stays at 1.
/// </summary>
public partial class SceneSelfTest
{
    /// <summary>The catalog part <see cref="BuildAndSave"/> stands on level 1.</summary>
    private const string RaisedType = "Mezzanine";

    private static string RaisedId => $"probe_{RaisedType.ToLowerInvariant()}";

    private void CheckLevels()
    {
        var editor = Editor!;

        // --- the file's level-1 part stands on level 1's plane
        float raisedY = editor.PositionOf(RaisedId)?.Y ?? float.NaN;
        Expect(Mathf.IsEqualApprox(raisedY, PartLayout.PlaneY(1)),
               $"a part the file puts on level 1 stands on level 1's work plane "
               + $"(y={raisedY:0.000}, want {PartLayout.PlaneY(1):0.000})");
        Expect(editor.LevelOf(RaisedId) == 1, $"and the editor says it is on level 1 (got {editor.LevelOf(RaisedId)})");
        Expect(Mathf.IsEqualApprox(editor.PositionOf("probe_conveyorbelt")?.Y ?? float.NaN,
                                   PartLayout.WorkPlaneY),
               "while a part with no level stays on the floor's plane");

        // Something to stand on: the deck's legs reach the ground from the
        // raised floor. Measured off the geometry, not the settings.
        if (editor.NodeFor(RaisedId)?.GetNodeOrNull<Node3D>("Legs") is { } legs
            && legs.GetChildCount() > 0 && legs.GetChild(0) is MeshInstance3D { Mesh: BoxMesh post } postNode)
        {
            float footY = (postNode.GlobalPosition - new Vector3(0, post.Size.Y / 2.0f, 0)).Y;
            Expect(Mathf.Abs(footY) < 0.01f,
                   $"the raised deck's legs reach the ground (their feet are at y={footY:0.000})");
        }
        else
        {
            Expect(false, "a deck on level 1 has legs down to the ground");
        }

        // --- the re-saved file: level 1, Y within the level, version 2
        const string resaved = "user://selftest_scene_levels.json";
        Expect(editor.SaveSceneToFile(resaved), "a scene with a raised part saves");
        string json = ReadAll(resaved);
        var data = SceneData.FromJson(json);
        var raised = data?.Parts.FirstOrDefault(p => p.Id == RaisedId);
        Expect(raised is not null, "and the raised part is in the file");
        if (raised is not null)
        {
            Expect(raised.Level == 1, $"stored on level 1 (got {raised.Level})");
            Expect(Mathf.IsEqualApprox(raised.Position[1], PartLayout.WorkPlaneY),
                   $"with its Y measured within its level, as every belt's 0.5 is "
                   + $"(got {raised.Position[1]:0.000})");
        }
        Expect(data?.Version == "2.0",
               $"and the file says it needs format 2 (got '{data?.Version}')");
        Expect(data?.Parts.Where(p => p.Id != RaisedId).All(p => p.Level == 0) == true,
               "while nothing else claims a level");

        // --- an older build refuses it (HP-07's check, as a version-1 reader)
        bool oldOpens = SceneData.TryParse(json, 1, out _, out string oldProblem);
        Expect(!oldOpens, "a version-1 reader refuses a scene with a raised part rather than "
                          + "putting it on the floor");
        Expect(oldProblem.Contains("version 2"),
               $"and says which version it is ('{oldProblem}')");
        Expect(SceneData.TryParse(json, out _, out string newProblem),
               $"while this build opens it ({newProblem})");

        // --- a scene with nothing raised is written exactly as before
        editor.SelectPartForInspection(RaisedId);
        editor.DeleteSelectedPart();
        const string flat = "user://selftest_scene_flat.json";
        Expect(editor.SaveSceneToFile(flat), "the same scene without its raised part saves");
        string flatJson = ReadAll(flat);
        Expect(SceneData.FromJson(flatJson)?.Version == "1.0",
               "as format 1, so the build before this one still opens it");
        Expect(!flatJson.Contains("\"level\""),
               "with no level key anywhere -- byte for byte what a version-1 build wrote");
        Expect(SceneData.TryParse(flatJson, 1, out _, out string flatProblem),
               $"and a version-1 reader accepts it ({flatProblem})");

        CheckPlacingOnALevel();

        // Put the every-part scene back for the checks after this one.
        editor.SetActiveLevel(0);
        Expect(editor.LoadSceneFromFile(ScenePath), "the every-part scene reloads after the level checks");
    }

    /// <summary>The editor side: place on a chosen level, lift a selection,
    /// undo it, drag along a raised plane, and paste a two-level group.</summary>
    private void CheckPlacingOnALevel()
    {
        var editor = Editor!;
        editor.ClearAllPlacedParts();
        editor.SetMode(EditorMode.Edit);

        // Place on level 1.
        editor.SetActiveLevel(1);
        Expect(editor.ActiveLevel == 1, "the active level can be set to 1");
        editor.SetPlacementPart("ConveyorBelt");
        editor.PlacePreviewAt(new Vector3(2.0f, 0.0f, 6.0f));
        editor.CancelPlacement();
        var ids = editor.PlacedPartIds();
        Expect(ids.Count == 1, $"one belt was placed (got {ids.Count})");
        if (ids.Count != 1) return;
        string upper = ids[0];
        Expect(Mathf.IsEqualApprox(editor.PositionOf(upper)?.Y ?? float.NaN, PartLayout.PlaneY(1)),
               $"a part placed with level 1 active lands on level 1's plane (y={editor.PositionOf(upper)?.Y:0.000})");

        // Place on the floor, then lift it a level with the selection.
        editor.SetActiveLevel(0);
        editor.SetPlacementPart("ConveyorBelt");
        editor.PlacePreviewAt(new Vector3(-2.0f, 0.0f, 6.0f));
        editor.CancelPlacement();
        string lower = editor.PlacedPartIds().First(id => id != upper);
        Expect(editor.LevelOf(lower) == 0, "level 0 placement lands on the floor's plane");

        editor.SelectPartForInspection(lower);
        editor.StepLevel(+1);
        Expect(editor.LevelOf(lower) == 1,
               $"PgUp with a part selected lifts it a level (now on {editor.LevelOf(lower)})");
        Expect(editor.ActiveLevel == 1, "and the active level follows it");
        editor.Undo();
        Expect(editor.LevelOf(lower) == 0, "and one Ctrl+Z puts it back on the floor");

        editor.SelectPartForInspection(lower);
        editor.StepLevel(-1);
        Expect(editor.LevelOf(lower) == 0, "a part cannot be lowered below the floor");

        // A drag on a raised part slides it along its own plane.
        Vector3 before = editor.PositionOf(upper)!.Value;
        var from = new Vector3(before.X, 8.0f, before.Z);
        Expect(editor.BeginPartDragAtRay(from, Vector3.Down), "the raised belt can be grabbed from above");
        var aim = new Vector3(before.X + 1.0f, 8.0f, before.Z);
        editor.DragPartToRay(aim, (new Vector3(before.X + 1.0f, PartLayout.PlaneY(1), before.Z) - aim).Normalized());
        editor.EndPartDrag();
        Vector3 after = editor.PositionOf(upper)!.Value;
        Expect(Mathf.IsEqualApprox(after.Y, PartLayout.PlaneY(1)) && Mathf.IsEqualApprox(after.X, before.X + 1.0f),
               $"a drag moves a raised part along level 1's plane (from {before} to {after})");

        // A copied group spanning two levels pastes on both.
        editor.SelectPartForInspection(upper);
        editor.ToggleSelectionByIndex(editor.PlacedPartIds().ToList().IndexOf(lower));
        editor.CopySelection();
        editor.PasteClipboard();
        var pasted = editor.SelectedInstanceIds();
        var levels = pasted.Select(id => editor.LevelOf(id) ?? -1).OrderBy(l => l).ToArray();
        Expect(levels.SequenceEqual(new[] { 0, 1 }),
               $"a pasted group keeps each part on its own level (got [{string.Join(",", levels)}])");
    }
}
