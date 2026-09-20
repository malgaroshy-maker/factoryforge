using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check on the loop a person is in while they build a line (BF-06):
///
/// <code>godot --headless --path engine -- --self-test=buildflow</code>
///
/// What is asserted is the *loop*, not the calls. "SetPlacementPart sets a
/// field" is true by construction; "a second click after a placement makes a
/// second part" is the thing a user is doing, and it is what was broken —
/// along with Ctrl+D, which put its copy one grid cell from the original and
/// selected nothing, so the second press duplicated the original again and
/// stacked two parts in one cell with no sign on screen that it had.
/// </summary>
public partial class BuildFlowSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

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
        // One tick of grace: parts build their geometry in _Ready, and the
        // duplicate offset is measured from those meshes.
        if (++_step != 2 || _done) return;
        _done = true;

        try
        {
            Editor.SetMode(EditorMode.Edit);
            Editor.ClearAllPlacedParts();

            CheckPlacementStaysArmed();
            CheckEscapePutsItDown();
            CheckDuplicateWalksALine();
            CheckNudge();
            CheckMoveDisarms();
            CheckGroupEdits();
            CheckSelectAllAndClipboard();
            CheckPartNames();
            CheckUndoOfDeleteKeepsSettings();
            CheckUndoOfMovePutsItBack();
            CheckRedoOfDuplicateKeepsIdentity();
            CheckHistorySurvivesADeletedNode();
        }
        catch (System.Exception ex)
        {
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        if (_failures.Count == 0)
        {
            GD.Print("self-test buildflow: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test buildflow: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private int PartCount() => Editor.PlacedPartIds().Count;

    // ---------- BF-01

    private void CheckPlacementStaysArmed()
    {
        Editor.SetPlacementPart("ConveyorBelt");
        Expect(Editor.HasPlacementPreview, "arming the tool makes a ghost");
        Expect(Editor.ArmedPartType == "ConveyorBelt", "and the editor says what it is holding");

        int before = PartCount();
        Editor.PlacePreviewAt(new Vector3(0, 0, 0));
        Expect(PartCount() == before + 1, "a click places a part");

        // The whole of BF-01: no second trip to the palette.
        Expect(Editor.HasPlacementPreview,
               "and the tool is still holding one, ready for the next cell");
        Expect(Editor.ArmedPartType == "ConveyorBelt", "still the same part");

        Editor.PlacePreviewAt(new Vector3(2.0f, 0, 0));
        Editor.PlacePreviewAt(new Vector3(4.0f, 0, 0));
        Expect(PartCount() == before + 3,
               $"three clicks make three parts (got {PartCount() - before})");

        // Distinct ids, because a repeat placement that adopted the first
        // part's tags would give three belts one `rotate` between them.
        var ids = new HashSet<string>(Editor.PlacedPartIds());
        Expect(ids.Count == PartCount(),
               $"each one gets its own instance id ({ids.Count} ids for {PartCount()} parts)");
    }

    /// <summary>A rotation set while placing survives the placement. A tool
    /// that silently springs back to 0° after every drop is worse than one
    /// that never rotated, because a line of turned belts comes out with one
    /// of them straight.</summary>
    private void CheckRotationSurvives()
    {
        Editor.RotatePreview();
        float turned = Editor.PreviewRotationY;
        Editor.PlacePreviewAt(new Vector3(6.0f, 0, 0));
        Expect(Mathf.IsEqualApprox(Editor.PreviewRotationY, turned),
               "the rotation you set while placing is still set for the next one");
    }

    // ---------- BF-01, the other half

    private void CheckEscapePutsItDown()
    {
        CheckRotationSurvives();

        Editor.CancelPlacement();
        Expect(!Editor.HasPlacementPreview, "Escape puts the part down");
        Expect(Editor.ArmedPartType is null, "and the editor says it is holding nothing");

        int before = PartCount();
        Editor.PlacePreviewAt(new Vector3(8.0f, 0, 0));
        Expect(PartCount() == before,
               "a click with nothing in hand places nothing");
    }

    // ---------- BF-02

    private void CheckDuplicateWalksALine()
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(new Vector3(0, 0, 0));
        Editor.CancelPlacement();

        Editor.SelectPartByIndex(0);
        Expect(Editor.SelectedInstanceId is not null, "the placed belt can be selected");

        Editor.DuplicateSelectedPart();
        Expect(PartCount() == 2, "Ctrl+D makes a copy");
        Expect(Editor.SelectedInstanceId != null && Editor.SelectedPosition is not null,
               "and the copy is what is now selected");

        Vector3 first = Editor.SelectedPosition!.Value;
        Expect(first.X >= 1.5f - 0.01f,
               $"the copy lands clear of the belt it came from, not inside it (x={first.X:0.00})");

        Editor.DuplicateSelectedPart();
        Expect(PartCount() == 3, "a second Ctrl+D makes a third");

        Vector3 second = Editor.SelectedPosition!.Value;
        Expect(second.X >= first.X + 1.5f - 0.01f,
               $"and it walks on from the copy rather than stacking on the original "
               + $"(x={second.X:0.00}, previous {first.X:0.00})");

        // The bug this replaces, stated as a check: three duplicates used to
        // occupy two cells, because nothing selected the copy.
        var cells = new HashSet<string>();
        foreach (var id in Editor.PlacedPartIds())
        {
            if (Editor.NodeFor(id) is { } node)
                cells.Add($"{node.Position.X:0.00},{node.Position.Z:0.00}");
        }
        Expect(cells.Count == 3, $"three parts stand in three cells (got {cells.Count})");

        // Every part on the grid it was placed on, and on the work plane.
        foreach (var id in Editor.PlacedPartIds())
        {
            if (Editor.NodeFor(id) is not { } node) continue;
            Expect(Mathf.IsEqualApprox(node.Position.Y, PartLayout.WorkPlaneY),
                   $"{id} sits on the work plane");
        }
    }

    // ---------- BF-03

    private void CheckNudge()
    {
        Editor.SelectPartByIndex(0);
        Vector3 before = Editor.SelectedPosition!.Value;

        Editor.NudgeSelectedPart(new Vector2(1, 0));
        Vector3 after = Editor.SelectedPosition!.Value;
        float moved = before.DistanceTo(after);

        Expect(moved > 0.01f, "an arrow key moves the selected part");
        Expect(Mathf.IsEqualApprox(moved, 0.5f),
               $"by exactly one grid cell (moved {moved:0.000} m)");
        Expect(Mathf.IsEqualApprox(after.Y, PartLayout.WorkPlaneY),
               "and keeps it on the work plane");

        Editor.Undo();
        Expect(Editor.SelectedPosition!.Value.IsEqualApprox(before),
               "one Ctrl+Z puts one nudge back");

        // Four nudges round the compass return to where they started, which is
        // the claim that the screen-relative mapping is a rotation and not four
        // unrelated directions.
        Vector3 start = Editor.SelectedPosition!.Value;
        Editor.NudgeSelectedPart(new Vector2(1, 0));
        Editor.NudgeSelectedPart(new Vector2(0, 1));
        Editor.NudgeSelectedPart(new Vector2(-1, 0));
        Editor.NudgeSelectedPart(new Vector2(0, -1));
        Expect(Editor.SelectedPosition!.Value.IsEqualApprox(start),
               "right, up, left, down comes back to where it started");
    }

    // ---------- ES-01, ES-03: several parts at once

    /// <summary>
    /// A selection is a group, and everything that can sensibly happen to
    /// several parts at once happens to all of them as **one undo step**.
    ///
    /// The box-select half of ES-02 cannot be reached here: it projects each
    /// part's bounding box through a camera, and a headless run has none. What
    /// is checked is everything downstream of that — a selection built by the
    /// Shift+click path, and the group edits that act on it.
    /// </summary>
    private void CheckGroupEdits()
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(new Vector3(0, 0, 0));
        Editor.PlacePreviewAt(new Vector3(2.0f, 0, 0));
        Editor.PlacePreviewAt(new Vector3(4.0f, 0, 0));
        Editor.CancelPlacement();

        Editor.SelectPartByIndex(0);
        Expect(Editor.SelectionCount == 1, "a plain selection holds one part");

        Editor.ToggleSelectionByIndex(1);
        Editor.ToggleSelectionByIndex(2);
        Expect(Editor.SelectionCount == 3,
               $"Shift+click adds to the selection (got {Editor.SelectionCount})");

        Editor.ToggleSelectionByIndex(1);
        Expect(Editor.SelectionCount == 2, "and clicking one again takes it out");
        Editor.ToggleSelectionByIndex(1);

        // --- nudge
        var before = new List<Vector3>(Editor.SelectedPositions());
        Editor.NudgeSelectedPart(new Vector2(0, 1));
        var after = Editor.SelectedPositions();

        bool allMoved = true;
        Vector3 step = after[0] - before[0];
        for (int i = 0; i < after.Count; i++)
        {
            if (!(after[i] - before[i]).IsEqualApprox(step)) allMoved = false;
        }
        Expect(step.Length() > 0.01f, "an arrow key moves a group");
        Expect(allMoved, "and moves every part by the same vector, so the group keeps its shape");

        Editor.Undo();
        var restored = Editor.SelectedPositions();
        bool back = true;
        for (int i = 0; i < restored.Count; i++)
        {
            if (!restored[i].IsEqualApprox(before[i])) back = false;
        }
        Expect(back, "and one Ctrl+Z puts the whole group back, not one part of it");

        // --- rotate
        var headingsBefore = new List<float>(Editor.SelectedHeadings());
        Editor.RotateSelectedPart();
        var headingsAfter = Editor.SelectedHeadings();
        bool allTurned = true;
        for (int i = 0; i < headingsAfter.Count; i++)
        {
            if (!Mathf.IsEqualApprox(Mathf.Wrap(headingsAfter[i] - headingsBefore[i], -Mathf.Pi, Mathf.Pi),
                                     Mathf.Pi / 2.0f))
                allTurned = false;
        }
        Expect(allTurned, "R turns every selected part a quarter turn about its own centre");
        Editor.Undo();

        // --- duplicate
        var shape = new List<Vector3>(Editor.SelectedPositions());
        int wasCount = PartCount();
        Editor.DuplicateSelectedPart();
        Expect(PartCount() == wasCount + shape.Count,
               $"Ctrl+D copies the whole group ({PartCount() - wasCount} of {shape.Count})");
        Expect(Editor.SelectionCount == shape.Count, "and the copies become the selection");

        var copies = Editor.SelectedPositions();
        Vector3 groupOffset = copies[0] - shape[0];
        bool keptShape = true;
        for (int i = 0; i < copies.Count; i++)
        {
            if (!(copies[i] - shape[i]).IsEqualApprox(groupOffset)) keptShape = false;
        }
        Expect(groupOffset.Length() > 0.01f, "the copy lands clear of the original");
        // One offset for the group, not each part's own: a belt, its sensor and
        // its pusher have to stay lined up with each other.
        Expect(keptShape, "and the copy is the same shape as what it was copied from");

        Editor.Undo();
        Expect(PartCount() == wasCount, "one Ctrl+Z takes the whole copy back");

        // --- delete
        Editor.SelectPartByIndex(0);
        Editor.ToggleSelectionByIndex(1);
        int before2 = PartCount();
        Editor.DeleteSelectedPart();
        Expect(PartCount() == before2 - 2,
               $"Del removes every selected part (got {before2 - PartCount()} of 2)");
        Expect(Editor.SelectionCount == 0, "and leaves nothing selected");

        Editor.Undo();
        Expect(PartCount() == before2,
               $"and one Ctrl+Z brings the whole group back (got {PartCount()} of {before2})");
    }

    // ---------- NV-02

    private void CheckSelectAllAndClipboard()
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(new Vector3(0, 0, 0));
        Editor.PlacePreviewAt(new Vector3(2.0f, 0, 0));
        Editor.CancelPlacement();
        Editor.SetPlacementPart("PhotoelectricSensor");
        Editor.PlacePreviewAt(new Vector3(0, 0, 0.5f));
        Editor.CancelPlacement();

        Editor.SelectEverything();
        Expect(Editor.SelectionCount == 3,
               $"Ctrl+A selects every part (got {Editor.SelectionCount} of 3)");

        var shape = new List<Vector3>(Editor.SelectedPositions());
        Editor.CopySelection();
        Expect(Editor.ClipboardCount == 3, "Ctrl+C holds the whole selection");

        int before = PartCount();
        Editor.PasteClipboard();
        Expect(PartCount() == before + 3,
               $"Ctrl+V puts all of it down (got {PartCount() - before} of 3)");
        Expect(Editor.SelectionCount == 3, "and what landed is what is selected");

        var pasted = Editor.SelectedPositions();
        Vector3 offset = pasted[0] - shape[0];
        bool keptShape = true;
        for (int i = 0; i < pasted.Count; i++)
        {
            if (!(pasted[i] - shape[i]).IsEqualApprox(offset)) keptShape = false;
        }
        Expect(offset.Length() > 0.01f, "clear of what it was copied from");
        // A copied belt, its sensor and its pusher have to stay lined up with
        // each other, which is why the clipboard stores offsets from a group
        // anchor rather than absolute positions.
        Expect(keptShape, "and the same shape as what was copied");

        // Distinct ids, or a pasted belt would adopt the tags of the one it was
        // copied from and two parts would drive one `rotate`.
        var ids = new HashSet<string>(Editor.PlacedPartIds());
        Expect(ids.Count == PartCount(),
               $"every pasted part mints its own instance id ({ids.Count} for {PartCount()})");

        // A second paste walks on rather than landing on the first.
        Editor.PasteClipboard();
        var second = Editor.SelectedPositions();
        Expect(!second[0].IsEqualApprox(pasted[0]),
               "a second Ctrl+V lands somewhere new rather than on top of the first");

        Editor.Undo();
        Expect(PartCount() == before + 3, "one Ctrl+Z takes a whole paste back");
    }

    // ---------- NV-01

    private void CheckPartNames()
    {
        Expect(!Editor.PartNamesVisible, "part names start off");

        Editor.TogglePartNames();
        Expect(Editor.PartNamesVisible, "N turns them on");

        // The label carries the *instance id*, which is the tag prefix, and it
        // has to follow a rename — a stale name on screen points at tags that
        // no longer exist, which is worse than no name at all.
        string id = Editor.PlacedPartIds()[0];
        var node = Editor.NodeFor(id);
        Expect(node is not null, "the first part is still there to be named");
        Expect(LabelTextOf(node!) == id,
               $"the label reads the part's instance id (got '{LabelTextOf(node!)}', want '{id}')");

        Editor.SelectPartByIndex(0);
        if (Editor.TryRenameSelectedPart("relabelled", out _))
        {
            Expect(LabelTextOf(node!) == "relabelled",
                   $"and follows a rename (got '{LabelTextOf(node!)}')");
        }

        // Nothing that measures a part may see the label: PartBounds reads
        // MeshInstance3D and a Label3D is not one, so the selection outline,
        // the click box and the duplicate offset are all unchanged by it.
        Editor.SelectPartByIndex(0);
        Vector3 wasAt = Editor.SelectedPosition!.Value;
        Editor.DuplicateSelectedPart();
        float step = Editor.SelectedPosition!.Value.X - wasAt.X;
        Expect(step >= 1.5f - 0.01f,
               $"and does not grow the part it names (duplicate stepped {step:0.00} m)");
        Editor.Undo();

        Editor.TogglePartNames();
        Expect(!Editor.PartNamesVisible, "N turns them off again");
    }

    private static string LabelTextOf(Node3D part) =>
        part.GetNodeOrNull<Label3D>("PartNameLabel")?.Text ?? "";

    // ---------- HP-03, HP-04, HP-05, HP-20, HP-37: what undo gives back

    /// <summary>Lay down one belt, tuned to something no default would be, and
    /// return its id. 0.23 m/s is deliberately not a round number: a default
    /// that happened to match would make every assertion below meaningless.</summary>
    private const float TunedSpeed = 0.23f;

    private string PlaceOneTunedBelt(Vector3 at)
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(at);
        Editor.CancelPlacement();

        string id = Editor.PlacedPartIds()[0];
        if (Editor.NodeFor(id) is ConveyorBelt belt)
        {
            Expect(!Mathf.IsEqualApprox(belt.Speed, TunedSpeed),
                   "the tuned speed differs from the default, or this test proves nothing");
            belt.Speed = TunedSpeed;
        }
        else
        {
            Expect(false, "the placed part is a ConveyorBelt");
        }
        return id;
    }

    private float SpeedOf(string id) =>
        Editor.NodeFor(id) is ConveyorBelt belt ? belt.Speed : float.NaN;

    /// <summary>
    /// HP-03. The console prints "Ctrl+Z to put them back" when you delete, and
    /// what came back was a factory-default part wearing the right name: the
    /// command stored type, position, rotation and id, and respawned through a
    /// path that never applied any properties. A belt you had slowed to a crawl
    /// came back at full speed, which is the kind of loss you discover twenty
    /// minutes later while wondering why the line behaves differently.
    /// </summary>
    private void CheckUndoOfDeleteKeepsSettings()
    {
        string id = PlaceOneTunedBelt(new Vector3(0, 0, 0));

        Editor.SelectPartByIndex(0);
        Editor.DeleteSelectedPart();
        Expect(PartCount() == 0, "Del removes the belt");

        Editor.Undo();
        Expect(PartCount() == 1, "Ctrl+Z brings a belt back");
        Expect(Editor.PlacedPartIds().Count == 1 && Editor.PlacedPartIds()[0] == id,
               $"under the same instance id (got '{string.Join(",", Editor.PlacedPartIds())}', want '{id}')");
        Expect(Mathf.IsEqualApprox(SpeedOf(id), TunedSpeed),
               $"and still tuned to {TunedSpeed} m/s, not back at the factory default "
               + $"(got {SpeedOf(id):0.000})");
    }

    /// <summary>
    /// HP-04. M-move committed by destroying the part and recording a
    /// *placement* at the destination, so Ctrl+Z removed the placement and
    /// restored nothing: undoing a move you were only trying to adjust deleted
    /// the part outright. The part now never leaves the scene, so the move undoes
    /// as a move — same node, same id, same settings.
    /// </summary>
    private void CheckUndoOfMovePutsItBack()
    {
        var from = new Vector3(0, PartLayout.WorkPlaneY, 0);
        string id = PlaceOneTunedBelt(from);

        Editor.SelectPartByIndex(0);
        Editor.StartMoveSelected();
        Expect(Editor.HasPlacementPreview, "M picks the part up");
        Editor.PlacePreviewAt(new Vector3(0, 0, 3.0f));

        Expect(PartCount() == 1, $"a move leaves one part, not two (got {PartCount()})");
        Expect(Editor.NodeFor(id) is { } moved && Mathf.IsEqualApprox(moved.Position.Z, 3.0f),
               "and the part is at the new cell");

        Editor.Undo();
        Expect(PartCount() == 1,
               $"Ctrl+Z after a move leaves the part in the scene rather than deleting it "
               + $"(got {PartCount()} parts)");
        Expect(Editor.NodeFor(id) is { } back && back.Position.IsEqualApprox(from),
               "and puts it back where it started");
        Expect(Mathf.IsEqualApprox(SpeedOf(id), TunedSpeed),
               $"with its settings intact (got {SpeedOf(id):0.000})");
    }

    /// <summary>
    /// HP-20, both halves. PartCommand already kept the id it minted across a
    /// redo, with a comment saying why: a fresh id silently breaks driver wiring
    /// pointing at the old one. Ctrl+D and Ctrl+V did not, and Ctrl+V goes
    /// through the *group* command, which the first draft of the item missed.
    /// </summary>
    private void CheckRedoOfDuplicateKeepsIdentity()
    {
        PlaceOneTunedBelt(new Vector3(0, 0, 0));

        Editor.SelectPartByIndex(0);
        Editor.DuplicateSelectedPart();
        string copyId = Editor.SelectedInstanceId ?? "";
        Expect(copyId.Length > 0, "Ctrl+D selects the copy it made");

        Editor.Undo();
        Editor.Redo();
        Expect(Editor.SelectedInstanceId == copyId,
               $"redoing a duplicate restores the same instance id "
               + $"(got '{Editor.SelectedInstanceId}', want '{copyId}')");

        // Ctrl+V, which is the group path: one item, but DuplicateGroupCommand.
        Editor.SelectEverything();
        Editor.CopySelection();
        Editor.PasteClipboard();
        var pasted = new List<string>(Editor.SelectedInstanceIds());
        Expect(pasted.Count > 0, "Ctrl+V puts something down");

        Editor.Undo();
        Editor.Redo();
        var again = Editor.SelectedInstanceIds();
        bool same = again.Count == pasted.Count;
        for (int i = 0; same && i < again.Count; i++)
        {
            if (again[i] != pasted[i]) same = false;
        }
        Expect(same,
               $"redoing a paste restores the same ids rather than minting new ones "
               + $"(got [{string.Join(",", again)}], want [{string.Join(",", pasted)}])");
    }

    /// <summary>
    /// HP-05 and HP-37 together, as the plan states the reproduction: nudge a
    /// group, delete it, Ctrl+Z twice.
    ///
    /// The move commands held Node3D references. Undoing the delete rebuilt each
    /// part as a *new* node, so the second Ctrl+Z wrote a position into an object
    /// Godot had freed — and the history popped the entry before invoking it, so
    /// the throw took the step with it. Resolving by part key instead means the
    /// command written before the delete finds the part the undo brought back.
    /// </summary>
    private void CheckHistorySurvivesADeletedNode()
    {
        Editor.ClearAllPlacedParts();
        Editor.SetPlacementPart("ConveyorBelt");
        Editor.PlacePreviewAt(new Vector3(0, 0, 0));
        Editor.PlacePreviewAt(new Vector3(2.0f, 0, 0));
        Editor.CancelPlacement();

        Editor.SelectPartByIndex(0);
        Editor.ToggleSelectionByIndex(1);
        var before = new List<Vector3>(Editor.SelectedPositions());
        var ids = new List<string>(Editor.SelectedInstanceIds());

        Editor.NudgeSelectedPart(new Vector2(1, 0));
        Editor.SelectPartByIndex(0);
        Editor.ToggleSelectionByIndex(1);
        Editor.DeleteSelectedPart();
        Expect(PartCount() == 0, "Del removes the nudged group");

        Editor.Undo();                       // undo the delete
        Expect(PartCount() == 2, $"the first Ctrl+Z brings the group back (got {PartCount()})");

        Editor.Undo();                       // undo the nudge, across the rebuild
        for (int i = 0; i < ids.Count; i++)
        {
            var node = Editor.NodeFor(ids[i]);
            Expect(node is not null && node.Position.IsEqualApprox(before[i]),
                   $"the second Ctrl+Z reaches {ids[i]} through the rebuild "
                   + $"(at {node?.Position}, want {before[i]})");
        }
    }

    // ---------- BF-01's exception

    private void CheckMoveDisarms()
    {
        Editor.SelectPartByIndex(0);
        int before = PartCount();

        Editor.StartMoveSelected();
        Expect(Editor.HasPlacementPreview, "M picks the part up");

        Editor.PlacePreviewAt(new Vector3(0, 0, 3.0f));
        Expect(PartCount() == before,
               $"putting it down moves it rather than copying it (was {before}, now {PartCount()})");
        // The exception to BF-01, and the reason it has to be one: a re-armed
        // ghost here would drop a second copy of the part you just moved on the
        // very next click.
        Expect(!Editor.HasPlacementPreview, "and the tool is empty afterwards, not holding a copy");
    }
}
