using System.Collections.Generic;
using FactoryForge.Parts;
using FactoryForge.Scenes;
using FactoryForge.TagBus;
using FactoryForge.View;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Handles interactive 3D part placement, rotation (R), deletion (Delete), and grid snapping on VoxelGrid.
/// </summary>
public partial class SceneEditor : Node3D, IPartHost
{
    [Export] public VoxelGrid Grid { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;
    public TagInspectorUI TagInspector { get; set; } = null!;

    /// <summary>The deterministic scene, when one is running. A conveyor part
    /// that is a view of it drives its transport speed, so changing the belt's
    /// speed in the inspector actually moves the boxes rather than only
    /// scrolling the tread texture faster.</summary>
    public SortingScene? Scene { get; set; }
    public PartPropertyInspectorUI PropertyInspector { get; set; } = null!;

    /// <summary>The dismissible hint bar, used to say what a freshly selected
    /// part can do (OP-09). Optional: headless runs have no UI at all.</summary>
    public IdleHintUI? IdleHint { get; set; }

    /// <summary>Set by Main so Ctrl+S / Ctrl+O can open the same dialogs the
    /// toolbar buttons do — one dialog implementation, two ways to reach it.
    /// See FF-21.</summary>
    public SceneToolbarUI? Toolbar { get; set; }

    /// <summary>Raised when the mode changes, so the toolbar and palette follow
    /// it rather than each keeping their own idea of what mode we are in.</summary>
    [Signal] public delegate void ModeChangedEventHandler(bool running);

    /// <summary>Edit or Run. See <see cref="EditorMode"/> for why a click needs
    /// to mean one thing at a time.</summary>
    public EditorMode Mode { get; private set; } = EditorMode.Edit;

    /// <summary>Which part type the placement tool currently holds, or the
    /// empty string when it holds nothing (BF-04).
    ///
    /// The palette follows this rather than remembering what its own button
    /// press did, because the tool is put down by things the palette never
    /// hears about: Escape, a right-click, entering Run mode, and committing a
    /// move. A highlight that only the button could clear would be left lit
    /// after every one of them.</summary>
    [Signal] public delegate void PlacementArmedChangedEventHandler(string partType);

    /// <summary>How many parts are selected, whenever that changes. The
    /// toolbar says so: a group move with nothing on screen to say how large
    /// the group is is a group move nobody trusts.</summary>
    [Signal] public delegate void SelectionChangedEventHandler(int count);

    /// <summary>The box-select rectangle in screen pixels, and whether one is
    /// being drawn. Emitted rather than drawn here because <c>SceneEditor</c> is
    /// a <c>Node3D</c> and the rectangle is 2D — see
    /// <see cref="SelectionRectUI"/>.</summary>
    [Signal] public delegate void SelectionRectChangedEventHandler(Rect2 rect, bool active);

    /// <summary>A save that did not land, with the reason. Raised rather than
    /// only logged: somebody who has just pressed Save is looking at the window,
    /// not at the console, and the old code told them "Saved scene" either way
    /// (HP-01).</summary>
    [Signal] public delegate void SaveFailedEventHandler(string path, string problem);

    /// <summary>A scene file that was refused, with the reason. The open scene
    /// is untouched when this is raised (HP-02).</summary>
    [Signal] public delegate void LoadFailedEventHandler(string path, string problem);

    /// <summary>A scene that opened but is not all there: it named part types
    /// this build does not have, and they were left out. <paramref name="types"/>
    /// is a comma-separated list.</summary>
    [Signal] public delegate void SceneLoadIncompleteEventHandler(string path, string types);

    private string? _activePartType;
    private Node3D? _previewNode;
    private float _previewRotationY;
    /// <summary>
    /// A part in the scene. <paramref name="OwnsTags"/> distinguishes a part the
    /// editor registered tags for from one that is only a *view* of tags the
    /// simulation owns: deleting the default belt must not delete
    /// conveyor.rotate, which SortingScene writes on every tick.
    ///
    /// <paramref name="Key"/> is the part's identity as far as the undo history
    /// is concerned — see <see cref="NextPartKey"/>. It is deliberately not the
    /// node, not the instance id and not the type-and-position pair the commands
    /// each used to pick for themselves.
    /// </summary>
    private sealed record PlacedPart(Node3D Node, string InstanceId, string PartType,
                                     bool OwnsTags, long Key)
    {
        private Dictionary<string, string>? _tagIds;

        /// <summary>
        /// Suffix -> full "{InstanceId}.{suffix}" id, for the fixed set of
        /// tags this part type's dispatch reads or writes every physics
        /// tick. Built once, on first access, instead of a fresh string
        /// concatenation per tag per part per tick — the actual allocation
        /// FF-15 measured (~4,500/sec on a 30-part scene).
        ///
        /// Rename must call <see cref="InvalidateTagIds"/>: a record's
        /// <c>with</c> expression copies this cache along with everything
        /// else, so without that a renamed part would keep dispatching
        /// against its old ids.
        /// </summary>
        public Dictionary<string, string> TagIds => _tagIds ??= BuildTagIdCache(InstanceId, PartType);

        public void InvalidateTagIds() => _tagIds = null;

        private static Dictionary<string, string> BuildTagIdCache(string instanceId,
                                                                  string partType)
        {
            // Asked of the part, through the catalog, rather than read from a
            // hand-kept table here (HP-34). That table was a second copy of the
            // registration switch, and a part whose tags changed in one and not
            // the other dispatched against ids nothing owned.
            var suffixes = PartCatalog.TagSuffixes(partType);
            var cache = new Dictionary<string, string>(suffixes.Count);
            foreach (string suffix in suffixes) cache[suffix] = $"{instanceId}.{suffix}";
            return cache;
        }
    }

    /// <summary>
    /// Everything selected, in the order it was added (ES-01).
    ///
    /// <see cref="_selectedPart"/> is the *primary* — the last one added — and
    /// it is what the property inspector, the rename box and the M key act on,
    /// because all three are about one part by nature. Everything that can
    /// sensibly happen to several parts at once (move, nudge, rotate,
    /// duplicate, delete) walks this list instead, as one undo step.
    ///
    /// The two are kept in step through <see cref="SelectOnly"/>,
    /// <see cref="ToggleSelection"/> and <see cref="DeselectPart"/> rather than
    /// by assignment, so the primary can never end up outside the selection it
    /// is supposed to be the head of.
    /// </summary>
    private readonly List<PlacedPart> _selection = new();

    private PlacedPart? _selectedPart;
    private readonly List<PlacedPart> _placedParts = new();

    /// <summary>
    /// One definition of part identity, for every command in the history
    /// (HP-37).
    ///
    /// The four command families each used to answer "which part?" differently
    /// and all four answers were wrong somewhere. Placement and deletion matched
    /// on type *and position* — so two overlapping conveyors were
    /// indistinguishable, and undoing a delete could resurrect the wrong one.
    /// Move and rotate held a <c>Node3D</c>, which does not survive a delete and
    /// its undo, because that rebuilds the part as a new node and leaves the
    /// earlier commands writing into a freed object. Duplicate held a snapshot
    /// and minted a fresh instance id on every redo.
    ///
    /// The key is none of those. It is minted once per part, carried across a
    /// rename (a record's <c>with</c> copies it), and restored by the command
    /// that respawns a part it previously removed — so a command written before
    /// a delete still finds the part after the undo that brought it back.
    /// </summary>
    private long _nextPartKey;

    private long NextPartKey() => ++_nextPartKey;

    /// <summary>The part a command is talking about, or null if it is gone.</summary>
    private PlacedPart? FindPlaced(long key)
    {
        if (key == 0) return null;
        int index = _placedParts.FindIndex(p => p.Key == key);
        return index < 0 ? null : _placedParts[index];
    }

    /// <summary>Everything a command needs to rebuild a part: identity,
    /// transform and every setting <see cref="PartProperties"/> knows about.
    /// The same shape a scene file stores, rebuilt by the same call a scene
    /// load uses, so "put it back" cannot mean something different here from
    /// what it means there.</summary>
    private static PartInstanceData Snapshot(PlacedPart part) => new()
    {
        Id = part.InstanceId,
        Type = part.PartType,
        Position = new[] { part.Node.Position.X, part.Node.Position.Y, part.Node.Position.Z },
        Rotation = new[] { part.Node.Rotation.X, part.Node.Rotation.Y, part.Node.Rotation.Z },
        Properties = PartProperties.Capture(part.Node),
    };

    /// <summary>Whichever part the cursor is over in Run mode, for the hover
    /// outline (UX-39) -- so a click's own hit test is not the first time a
    /// player learns a part is clickable.</summary>
    private Node3D? _hoveredNode;
    private MeshInstance3D? _hoverOutline;

    /// <summary>Emitters whose emit tag is currently high, for edge detection.</summary>
    private bool _emitAlternate;

    /// <summary>
    /// Raised whenever the set of tags changes — a part placed, deleted, renamed,
    /// or a whole scene loaded.
    ///
    /// A connected driver has a copy of the tag list from the last describe, so
    /// without this it never learns that the belt you just placed exists. The
    /// bus already knew how to republish (<c>SendDescribe</c> bumps the epoch);
    /// nothing was asking it to.
    /// </summary>
    [Signal] public delegate void TagsChangedEventHandler();

    /// <summary>
    /// Raised when a whole scene arrives — a template opened from the start
    /// screen, a file loaded, or the built-in demo registered — as opposed to
    /// one part being placed.
    ///
    /// It exists so the camera can frame what just appeared (CP-16). Framing on
    /// <see cref="TagsChanged"/> instead would also fire on every single
    /// placement, and a viewport that lurches every time you drop a sensor is
    /// worse than one that never moves. Opening a template used to leave the
    /// camera wherever it was, which for the default pose meant a control panel
    /// filling the frame and the line you had just chosen entirely off-screen.
    /// </summary>
    [Signal] public delegate void SceneLoadedEventHandler();

    /// <summary>The scene's name, as reported on the bus. Loading a file adopts
    /// the name it was saved under, so a driver is not told every custom line is
    /// the sorting demo.</summary>
    public string SceneName { get; private set; } = "sorting-by-height";

    /// <summary>Is a part currently following the cursor, waiting to be placed?</summary>
    public bool HasPlacementPreview => _previewNode is not null;

    /// <summary>The selected part's id, or null. Public for the self-test
    /// (<c>--self-test=modes</c>, UX-44) to check that entering Run clears a
    /// selection the same way it already clears a placement preview.</summary>
    public string? SelectedInstanceId => _selectedPart?.InstanceId;

    /// <summary>Anything placed right now, so Clear can skip its own
    /// confirmation when there is nothing to lose.</summary>
    public bool HasPlacedParts => _placedParts.Count > 0;

    /// <summary>True once the scene differs from what was last saved or
    /// loaded. Drives the unsaved-changes prompt on Home, Load, and quit —
    /// there is no reliable way to tell "safe to discard" apart from
    /// "about to lose twenty minutes of work" without it.</summary>
    public bool IsDirty { get; private set; }

    public void MarkDirty() => IsDirty = true;

    private void NotifyTagsChanged()
    {
        TagInspector?.RebuildTagList();
        EmitSignal(SignalName.TagsChanged);
    }

    public void ToggleMode() => SetMode(Mode == EditorMode.Edit ? EditorMode.Run : EditorMode.Edit);

    /// <summary>
    /// Switch mode. Entering Run drops anything half-done in the editor: a
    /// placement preview left floating under the cursor, or a selection whose
    /// gizmo would otherwise hang around a part you can no longer move. A move
    /// in progress is cancelled the same way Escape cancels it, so the part it
    /// started from stays where it is rather than being lost.
    /// </summary>
    public void SetMode(EditorMode mode)
    {
        if (Mode == mode) return;

        Mode = mode;
        if (mode == EditorMode.Run)
        {
            ClearPreview();
            DeselectPart();
        }
        else
        {
            ClearHoverHighlight();
        }

        EmitSignal(SignalName.ModeChanged, mode == EditorMode.Run);
        GD.Print(mode == EditorMode.Run
            ? "Run mode — click the controls to operate the line"
            : "Edit mode — click parts to select, move and delete");
    }

    public void SetPlacementPart(string partType)
    {
        // The palette is hidden in Run mode, but a stray signal must not sneak a
        // ghost part into a running line.
        if (Mode == EditorMode.Run) return;

        ClearPreview();
        ArmPreview(partType, 0f);
    }

    /// <summary>
    /// Build the ghost for a part type and take up the tool.
    ///
    /// Split out of <see cref="SetPlacementPart"/> because BF-01 needs to re-arm
    /// *without* resetting the rotation: a line of belts turned 90° should stay
    /// turned for the next one, and a tool that silently springs back to 0° on
    /// every placement is worse than one that never rotated.
    /// </summary>
    private void ArmPreview(string partType, float rotationY)
    {
        _activePartType = partType;
        _previewRotationY = rotationY;
        _previewNode = CreatePartNode(partType);

        if (_previewNode is not null)
        {
            _previewNode.Name = "PlacementPreview";
            _previewNode.Rotation = new Vector3(0, _previewRotationY, 0);
            AddChild(_previewNode);
            // After AddChild, not before: a part builds its collision shapes and
            // its Area3D in _Ready, which Godot runs as the node enters the
            // tree, so there is nothing to switch off until it has.
            MakeInert(_previewNode);
        }

        EmitSignal(SignalName.PlacementArmedChanged, _previewNode is null ? "" : partType);
    }

    /// <summary>
    /// Take a preview out of the simulation entirely (HP-41).
    ///
    /// The ghost is an ordinary part -- that is what makes it an honest preview,
    /// since it is built by the same factory and shows the real geometry -- and
    /// it was added to the scene as one. So sweeping a remover preview over the
    /// line *deleted cartons*: a remover zone connects BodyEntered in _Ready and calls
    /// QueueFree on whatever arrives, and nothing about cancelling the placement
    /// brings them back. The same is true in smaller ways of every part with a
    /// collider: a belt ghost blocked cartons it was not yet part of.
    ///
    /// This has to reach into the part rather than tint the root node, because
    /// the parts build these nodes themselves. Three things make a preview
    /// inert, and all three are needed: an Area3D that is still monitoring fires
    /// its signals whatever its layers say, a CollisionShape3D that is still
    /// enabled keeps a physical body solid, and a RigidBody3D would otherwise
    /// fall off the work plane while you are deciding where to put it.
    /// </summary>
    private static void MakeInert(Node node)
    {
        switch (node)
        {
            case Area3D area:
                area.Monitoring = false;
                area.Monitorable = false;
                area.CollisionLayer = 0;
                area.CollisionMask = 0;
                break;
            case RigidBody3D body:
                body.Freeze = true;
                body.CollisionLayer = 0;
                body.CollisionMask = 0;
                break;
            case CollisionObject3D solid:
                solid.CollisionLayer = 0;
                solid.CollisionMask = 0;
                break;
            case CollisionShape3D shape:
                shape.Disabled = true;
                break;
        }

        foreach (var child in node.GetChildren()) MakeInert(child);
    }

    /// <summary>
    /// Take this event out of everyone else's hands (ES-05).
    ///
    /// The orbit camera and the editor both listen on `_UnhandledInput`, and
    /// neither used to claim anything — so **dragging a part also orbited the
    /// view**, because both handlers saw the same motion and both acted on it.
    /// The part went where the cursor went and the world turned underneath it
    /// at the same time, which reads as the drag being broken rather than as
    /// two features fighting.
    ///
    /// Claimed only for the gestures the editor is actually using — a part
    /// drag and a selection box — so a plain left-drag on empty floor still
    /// orbits, which is the most-used gesture in the app and must not become
    /// collateral damage.
    /// </summary>
    private void ClaimInput() => GetViewport()?.SetInputAsHandled();

    public override void _UnhandledInput(InputEvent @event)
    {
        // F1 is the one binding that works in both modes; everything else below
        // is editing, and editing is exactly what Run mode switches off.
        if (@event is InputEventKey modeKey && modeKey.Pressed && !modeKey.Echo
            && modeKey.Keycode == Key.F1)
        {
            ToggleMode();
            return;
        }

        // Save and Open work in both modes, checked ahead of the Run-mode
        // return below. Undo/redo/move/rotate/delete are genuinely edit-only;
        // Ctrl+S silently doing nothing in Run mode was never a deliberate
        // choice, just a side effect of that same early return (§2.7 -> UX-40).
        if (@event is InputEventKey saveOpenKey && saveOpenKey.Pressed && !saveOpenKey.Echo
            && saveOpenKey.CtrlPressed)
        {
            if (saveOpenKey.Keycode == Key.S) { Toolbar?.ShowSaveDialog(); return; }
            if (saveOpenKey.Keycode == Key.O) { Toolbar?.ShowLoadDialog(); return; }
        }

        if (Mode == EditorMode.Run)
        {
            if (@event is InputEventMouseButton runClick && runClick.ButtonIndex == MouseButton.Left)
            {
                // The event's own position, not the live cursor: they agree for a
                // real click but only the event knows where the click happened,
                // which is the difference between a testable path and one that
                // can only be checked by hand.
                if (!runClick.Pressed) EndDialDrag();
                else if (FaultToolArmed) FaultAt(runClick.Position);
                else if (!BeginDialDragAt(runClick.Position)) PressControlAt(runClick.Position);
                ClaimInput();
            }
            else if (@event is InputEventKey runKey && runKey.Pressed && !runKey.Echo
                     && runKey.Keycode == Key.Escape)
            {
                SetFaultToolArmed(false);
            }
            else if (@event is InputEventMouseMotion runMotion)
            {
                // A pot is turned, not pressed, so the drag owns the mouse
                // until the button comes back up -- including the hover
                // highlight, which would otherwise chase whatever the cursor
                // wandered over mid-turn.
                // The pot is turned by dragging, and without claiming the
                // motion the camera orbits at the same time -- the same
                // collision ES-05 fixes in Edit mode, on the one Run-mode
                // gesture that is a drag rather than a click.
                if (IsDraggingDial) { DragDial(-runMotion.Relative.Y); ClaimInput(); }
                else UpdateHoverHighlight(runMotion.Position);
            }
            return;
        }

        if (_previewNode is not null && @event is InputEventMouseMotion)
        {
            UpdatePreviewPosition();
        }
        else if (_previewNode is not null && @event is InputEventMouseButton mouseBtn && mouseBtn.Pressed)
        {
            if (mouseBtn.ButtonIndex == MouseButton.Left)
            {
                PlaceCurrentPart();
            }
            else if (mouseBtn.ButtonIndex == MouseButton.Right)
            {
                ClearPreview();
            }
        }
        else if (_previewNode is null && @event is InputEventMouseButton clickBtn
                 && clickBtn.ButtonIndex == MouseButton.Left)
        {
            // Press selects and *arms* a move; release commits it. Everyone
            // tries dragging a part first, and until this landed the only way
            // to move one was the M key, which nothing on screen mentioned
            // (OP-08). A press that never travels is still a plain click, so
            // selecting did not have to change to make dragging work.
            if (clickBtn.Pressed)
            {
                // The event's own position, not the live cursor — the same
                // correction Run mode's dispatch already carries. They agree
                // for a real click, but a drag has to grab the part under the
                // *press*, and only the event knows where that was.
                if (PickPartAt(clickBtn.Position) is { } hit)
                {
                    if (clickBtn.ShiftPressed) ToggleSelection(hit);
                    // Pressing on something already selected keeps the whole
                    // group, so a group can be dragged by any member of it.
                    // Pressing on anything else selects just that one.
                    else if (!_selection.Contains(hit)) SelectOnly(hit);
                    ArmPartDrag(clickBtn.Position);
                    ClaimInput();
                }
                else if (clickBtn.CtrlPressed)
                {
                    // Ctrl, because a plain left-drag on empty floor is how the
                    // camera orbits and that is the most-used gesture in the
                    // app. Empty space without Ctrl still deselects on the
                    // press, exactly as it did before (ES-02).
                    BeginSelectionRect(clickBtn.Position, clickBtn.ShiftPressed);
                    ClaimInput();
                }
                else if (!clickBtn.ShiftPressed)
                {
                    DeselectPart();
                }
            }
            else if (_selectionRectActive)
            {
                EndSelectionRect(clickBtn.Position);
                ClaimInput();
            }
            else if (_partDrag is not null)
            {
                EndPartDrag();
                ClaimInput();
            }
        }
        else if (_previewNode is null && _selectionRectActive
                 && @event is InputEventMouseMotion rectMotion)
        {
            UpdateSelectionRect(rectMotion.Position);
            ClaimInput();
        }
        else if (_previewNode is null && _partDrag is not null
                 && @event is InputEventMouseMotion dragMotion)
        {
            UpdatePartDrag(dragMotion.Position);
            ClaimInput();
        }
        else if (@event is InputEventKey keyEvent && keyEvent.Pressed && !keyEvent.Echo)
        {
            HandleEditorKey(keyEvent);
        }
    }

    /// <summary>
    /// Edit mode's keyboard, and the one rule it has: a key this consumes must
    /// be claimed, and a key it does not want must be left alone (HP-38).
    ///
    /// Both halves were broken, in opposite directions and on adjacent lines.
    /// Ctrl+C copied and did not claim, so it fell through to <c>Main</c>'s
    /// bare <c>Key.C</c> and toggled the camera as well — one keystroke, two
    /// actions, one of which the user did not ask for and cannot undo. Ctrl+R
    /// rotated the selection because the rotate branch did not exclude Ctrl,
    /// while <c>Main</c> treats Ctrl+R as "reset the simulation" — so the reset
    /// also turned whatever was selected. Two lines below, <c>Key.N</c> was
    /// already guarded with <c>!keyEvent.CtrlPressed</c>: the convention existed
    /// and had been applied to one key out of three.
    ///
    /// Escape is deliberately not claimed. It cancels a placement here *and*
    /// dismisses an overlay in <c>Main</c>, and those are two different things
    /// that happen to share a key by long convention.
    /// </summary>
    private void HandleEditorKey(InputEventKey keyEvent)
    {
        bool handled = true;

        if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.Z)
        {
            Undo();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.Y)
        {
            Redo();
        }
            // Ctrl+S / Ctrl+O are handled above, ahead of the Run-mode return,
            // so they are unreachable here (Edit mode already returned via
            // that branch too) rather than duplicated.
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.D && _selectedPart is not null)
        {
            DuplicateSelectedPart();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.A)
        {
            SelectEverything();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.C)
        {
            CopySelection();
        }
        else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.V)
        {
            PasteClipboard();
        }
        else if (keyEvent.Keycode == Key.M && _selectedPart is not null)
        {
            StartMoveSelectedPart();
        }
        else if (keyEvent.Keycode == Key.R && !keyEvent.CtrlPressed && _previewNode is not null)
        {
            _previewRotationY += Mathf.Pi / 2.0f;
            _previewNode.Rotation = new Vector3(0, _previewRotationY, 0);
        }
        else if (keyEvent.Keycode == Key.R && !keyEvent.CtrlPressed
                 && _previewNode is null && _selectedPart is not null)
        {
            RotateSelectedPart();
        }
        else if (keyEvent.Keycode == Key.N && !keyEvent.CtrlPressed)
        {
            TogglePartNames();
        }
        else if (keyEvent.Keycode == Key.Escape)
        {
            ClearPreview();
            DeselectPart();
            // Shared with Main's overlay dismissal on purpose. See the summary.
            handled = false;
        }
        else if (_selectedPart is not null && !keyEvent.CtrlPressed
                 && NudgeFor(keyEvent.Keycode) is { } nudge)
        {
            NudgeSelectedPart(nudge);
        }
        else if (keyEvent.Keycode == Key.Delete || keyEvent.Keycode == Key.Backspace)
        {
            DeleteSelectedPart();
        }
        else
        {
            handled = false;
        }

        if (handled) ClaimInput();
    }

    /// <summary>Screen-space direction for an arrow key, or null for anything
    /// else. Kept as one table rather than four branches so the key list and
    /// the handler cannot disagree about which keys nudge.</summary>
    private static Vector2? NudgeFor(Key keycode) => keycode switch
    {
        Key.Left => new Vector2(-1, 0),
        Key.Right => new Vector2(1, 0),
        Key.Up => new Vector2(0, 1),
        Key.Down => new Vector2(0, -1),
        _ => null,
    };

    private readonly EditorCommandHistory _history = new();

    public void Undo()
    {
        bool did = _history.Undo();
        if (did) MarkDirty();
        GD.Print(did ? "Undid last editor action" : "Nothing to undo");
    }

    public void Redo()
    {
        bool did = _history.Redo();
        if (did) MarkDirty();
        GD.Print(did ? "Redid last editor action" : "Nothing to redo");
    }

    /// <summary>
    /// Place and delete as undoable steps. A freed node cannot be revived, so a
    /// command stores what the part *was* and rebuilds it on demand. That makes
    /// place and delete exact inverses of each other.
    ///
    /// "What the part was" is a whole <see cref="PartInstanceData"/> — the same
    /// record a scene file stores, rebuilt through <see cref="SpawnFromData"/>,
    /// the same call a scene load uses. It used to be type, position, rotation
    /// and id only, respawned through a separate <c>SpawnPart</c> that never
    /// applied any properties, so deleting a conveyor tuned to 0.2 m/s and
    /// pressing Ctrl+Z — which the console itself offers — brought back a
    /// factory-default belt (HP-03). The settings were lost at the moment the
    /// tool promised to put them back.
    ///
    /// The part key is remembered as well as the instance id, so a move command
    /// recorded before the delete still resolves after the undo (HP-05).
    /// </summary>
    private sealed class PartCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly PartInstanceData _data;
        private readonly bool _isPlacement;
        private long _key;

        public PartCommand(SceneEditor editor, PartInstanceData data, bool isPlacement,
                           long key = 0)
        {
            _editor = editor;
            _data = data;
            _isPlacement = isPlacement;
            _key = key;
        }

        public void Execute()
        {
            if (_isPlacement) Respawn();
            else _editor.RemovePart(_key);
        }

        public void Undo()
        {
            if (_isPlacement) _editor.RemovePart(_key);
            else Respawn();
        }

        private void Respawn()
        {
            var placed = _editor.SpawnFromData(_data, notify: true, reviveKey: _key);
            if (placed is null) return;

            // Remember the identity the first execute minted, so a redo restores
            // the same one. Without it a redo minted a fresh id and silently
            // broke any driver wiring pointing at the old one.
            if (_data.Id.Length == 0) _data.Id = placed.InstanceId;
            if (_key == 0) _key = placed.Key;
        }
    }

    /// <summary>Undo counterpart to <see cref="SpawnFromData"/>: drop the part
    /// with this key, if it is still there.
    ///
    /// It used to take a type and a position and remove the last part matching
    /// both, which cannot tell two overlapping conveyors apart and is HP-37's
    /// bug — undoing a placement could take away a part somebody had placed
    /// deliberately in the same cell.</summary>
    private void RemovePart(long key)
    {
        if (FindPlaced(key) is not { } part) return;

        if (_selectedPart == part) DeselectPart();
        ForgetPart(part);
        NotifyTagsChanged();
    }

    /// <summary>
    /// Clear the rigid-body scene back to its start state: despawn every carton
    /// and zero the removers. The machines themselves stay exactly where they
    /// are — resetting a simulation restarts the run, it does not undo the scene
    /// you built.
    /// </summary>
    public void ResetItems()
    {
        foreach (var node in GetParent()?.GetChildren() ?? new Godot.Collections.Array<Node>())
        {
            if (node is BoxPhysics box) box.QueueFree();
        }

        // Every part puts itself back (HP-34). This was eleven `is <Type>`
        // tests in a row, each one calling a differently-named reset method and
        // then writing that part's tags by hand -- so a new part with run state
        // was a part a reset silently left where the last run put it. Until
        // LP-12 that was the heating station: Ctrl+R left the plate hot and the
        // next run started from a place no experiment could reproduce.
        foreach (var part in _placedParts)
        {
            if (part.Node is IPart resettable)
                resettable.ResetPart(new PartReset(Tags, part.InstanceId));
        }

        _emitAlternate = false;
    }

    /// <summary>Write a part's tag if the scene actually has it. A part can be
    /// a *view* of tags the simulation owns, in which case it never registered
    /// its own and there is nothing here to write.</summary>
    private void SetIfPresent(string instanceId, string suffix, Variant value)
    {
        if (Tags is null) return;
        string id = $"{instanceId}.{suffix}";
        if (Tags.Contains(id)) Tags.Set(id, value);
    }

    /// <summary>Detach a part from the scene, taking its tags with it if it owns
    /// them. A view of simulation-owned tags leaves them alone.</summary>
    private void ForgetPart(PlacedPart part)
    {
        if (part.OwnsTags && Tags is not null)
            PartTagManager.UnregisterPartTags(part.InstanceId, Tags);

        part.Node.QueueFree();
        _placedParts.Remove(part);
        _dragOrigins.Remove(part);

        // A freed part must not stay in the selection: the gizmo drops invalid
        // nodes on its own, but a group edit would still walk over it and touch
        // a node that no longer exists.
        if (_selection.Remove(part))
        {
            if (_selection.Count == 0) DeselectPart();
            else RefreshSelection();
        }
    }

    /// <summary>
    /// Pick a part up. The original is only removed once the move is committed:
    /// deleting it up front meant cancelling with Escape destroyed the part
    /// outright, with the preview thrown away and nothing left to put back.
    /// </summary>
    private void StartMoveSelectedPart()
    {
        if (_selectedPart is not { } entry) return;

        SetPlacementPart(entry.PartType);
        if (_previewNode is not null)
        {
            _previewNode.Position = entry.Node.Position;
            _previewNode.Rotation = entry.Node.Rotation;
        }

        _movingPart = entry;
        DeselectPart();
    }

    /// <summary>The part under a held mouse button, and where it started.
    /// Armed on press rather than on the first motion, so the undo step knows
    /// the position the drag began from and not wherever the part had already
    /// slid to.</summary>
    private PlacedPart? _partDrag;
    private Vector2 _partDragFrom;
    private Vector3 _partDragOrigin;
    private bool _partDragMoved;

    /// <summary>Screen pixels a press has to travel before it counts as a drag
    /// rather than a click. Without a threshold, a click with a shaky hand
    /// would nudge the part it was only meant to select — and a two-pixel move
    /// is invisible until the scene is saved.</summary>
    private const float DragThresholdPixels = 6.0f;

    private void ArmPartDrag(Vector2 screenPosition)
    {
        if (Mode != EditorMode.Edit || _selectedPart is not { } selected) return;
        _partDrag = selected;
        _partDragFrom = screenPosition;
        _partDragOrigin = selected.Node.Position;
        _partDragMoved = false;
        CaptureDragOrigins();
    }

    /// <summary>Where every selected part stood when the drag began, so the
    /// rest of the group can follow the one under the cursor by the same
    /// vector. Recorded on arming rather than on the first motion, for the same
    /// reason the single-part drag records its origin there: the undo step has
    /// to know where the drag *started*, not wherever things had already slid
    /// to.</summary>
    private void CaptureDragOrigins()
    {
        _dragOrigins.Clear();
        foreach (var entry in _selection) _dragOrigins[entry] = entry.Node.Position;
    }

    private readonly Dictionary<PlacedPart, Vector3> _dragOrigins = new();

    private void UpdatePartDrag(Vector2 screenPosition)
    {
        if (_partDrag is null) return;
        if (!_partDragMoved && _partDragFrom.DistanceTo(screenPosition) < DragThresholdPixels)
            return;

        var camera = GetViewport().GetCamera3D();
        if (camera is null) return;

        _partDragMoved = true;
        DragPartToRay(camera.ProjectRayOrigin(screenPosition),
                      camera.ProjectRayNormal(screenPosition));
    }

    /// <summary>Move the part being dragged to wherever this ray meets the work
    /// plane. Split from the screen entry point so a headless self-test can
    /// drive a real drag with a synthetic ray and no camera (OP-08).</summary>
    public void DragPartToRay(Vector3 from, Vector3 dir)
    {
        if (_partDrag is null) return;
        if (WorkPlanePoint(from, dir) is not { } point) return;

        // The part under the cursor goes where the cursor is; everything else
        // selected follows by the same vector, so a group keeps its shape. The
        // delta is measured from the drag's origin rather than from the last
        // frame, so a drag that leaves and re-enters the work plane does not
        // accumulate error.
        Vector3 delta = point - _partDragOrigin;
        _partDrag.Node.Position = point;

        foreach (var (entry, origin) in _dragOrigins)
        {
            if (entry == _partDrag) continue;
            if (!IsInstanceValid(entry.Node)) continue;
            entry.Node.Position = origin + delta;
        }
    }

    /// <summary>Begin a drag on whatever part the ray hits, selecting it the
    /// same way a click would. The headless counterpart of a mouse press.</summary>
    public bool BeginPartDragAtRay(Vector3 from, Vector3 dir)
    {
        if (Mode != EditorMode.Edit) return false;
        SelectPartAtRay(from, dir);
        if (_selectedPart is not { } selected) return false;

        _partDrag = selected;
        _partDragFrom = Vector2.Zero;
        _partDragOrigin = selected.Node.Position;
        _partDragMoved = true;      // no screen travel to threshold against
        CaptureDragOrigins();
        return true;
    }

    public bool IsDraggingPart => _partDrag is not null;

    // ---------------------------------------------------------- box select

    private bool _selectionRectActive;
    private bool _selectionRectAdds;
    private Vector2 _selectionRectFrom;
    private Vector2 _selectionRectTo;

    /// <summary>Below this the press was a click, not a box — the same
    /// threshold a part drag uses, for the same reason: a hand shakes.</summary>
    private const float SelectionRectMinPixels = 6.0f;

    private void BeginSelectionRect(Vector2 screenPosition, bool adds)
    {
        _selectionRectActive = true;
        _selectionRectAdds = adds;
        _selectionRectFrom = screenPosition;
        _selectionRectTo = screenPosition;
    }

    private void UpdateSelectionRect(Vector2 screenPosition)
    {
        _selectionRectTo = screenPosition;
        EmitSignal(SignalName.SelectionRectChanged, CurrentSelectionRect(), true);
    }

    private Rect2 CurrentSelectionRect()
    {
        var topLeft = new Vector2(Mathf.Min(_selectionRectFrom.X, _selectionRectTo.X),
                                  Mathf.Min(_selectionRectFrom.Y, _selectionRectTo.Y));
        var size = (_selectionRectTo - _selectionRectFrom).Abs();
        return new Rect2(topLeft, size);
    }

    /// <summary>
    /// Finish a box (ES-02).
    ///
    /// A box that never grew is a click on empty space, and that still means
    /// "deselect" — which is why the press could not deselect on its own.
    /// </summary>
    private void EndSelectionRect(Vector2 screenPosition)
    {
        _selectionRectActive = false;
        _selectionRectTo = screenPosition;
        EmitSignal(SignalName.SelectionRectChanged, new Rect2(), false);

        Rect2 rect = CurrentSelectionRect();
        if (rect.Size.X < SelectionRectMinPixels && rect.Size.Y < SelectionRectMinPixels)
        {
            if (!_selectionRectAdds) DeselectPart();
            return;
        }

        SelectAll(PartsWithinRect(rect), _selectionRectAdds);
    }

    /// <summary>
    /// Every part whose on-screen extent meets the box.
    ///
    /// Tested against the part's projected bounding box rather than against its
    /// origin: a three-metre belt's origin is in the middle of it, so an origin
    /// test would refuse a box drawn neatly around one end of a line and
    /// silently include a belt whose visible body is entirely outside the box.
    ///
    /// Corners behind the camera are dropped rather than projected —
    /// <c>UnprojectPosition</c> mirrors those to the opposite side of the
    /// screen, which would make a part behind you intersect almost any box.
    /// </summary>
    private List<PlacedPart> PartsWithinRect(Rect2 rect)
    {
        var found = new List<PlacedPart>();
        var camera = GetViewport()?.GetCamera3D();
        if (camera is null) return found;

        foreach (var entry in _placedParts)
        {
            var box = PartBounds.Measure(entry.Node);
            Transform3D toWorld = entry.Node.GlobalTransform;

            bool any = false;
            Vector2 lo = Vector2.Zero;
            Vector2 hi = Vector2.Zero;

            for (int corner = 0; corner < 8; corner++)
            {
                Vector3 world = toWorld * box.GetEndpoint(corner);
                if (camera.IsPositionBehind(world)) continue;

                Vector2 screen = camera.UnprojectPosition(world);
                if (!any) { lo = screen; hi = screen; any = true; continue; }
                lo = new Vector2(Mathf.Min(lo.X, screen.X), Mathf.Min(lo.Y, screen.Y));
                hi = new Vector2(Mathf.Max(hi.X, screen.X), Mathf.Max(hi.Y, screen.Y));
            }

            if (!any) continue;
            if (rect.Intersects(new Rect2(lo, hi - lo))) found.Add(entry);
        }

        return found;
    }

    /// <summary>Commit the drag as one undoable step. A drag that never moved
    /// the part pushes nothing: Ctrl+Z after a click should undo whatever you
    /// did before the click, not a move that did not happen.</summary>
    public void EndPartDrag()
    {
        if (_partDrag is { } dragged && _partDragMoved
            && dragged.Node.Position != _partDragOrigin)
        {
            var moves = new List<IEditorCommand>(_dragOrigins.Count);
            foreach (var (entry, origin) in _dragOrigins)
            {
                if (!IsInstanceValid(entry.Node)) continue;
                if (entry.Node.Position == origin) continue;
                moves.Add(new MoveCommand(this, entry.Key, origin, entry.Node.Position));
            }
            if (moves.Count == 0)
                moves.Add(new MoveCommand(this, dragged.Key, _partDragOrigin, dragged.Node.Position));

            _history.ExecuteCommand(CompositeCommand.Of(moves));
            MarkDirty();
            GD.Print(moves.Count == 1
                ? $"Moved {dragged.InstanceId} (Ctrl+Z to put it back)"
                : $"Moved {moves.Count} parts (Ctrl+Z to put them back)");
        }

        _partDrag = null;
        _partDragMoved = false;
        _dragOrigins.Clear();
    }

    /// <summary>
    /// Several edits that undo and redo as one.
    ///
    /// Every group action here is a list of the single-part commands that
    /// already existed, which is deliberate: deleting five parts is five
    /// deletes, and reusing the command that was already correct for one is
    /// less to get wrong than a second, bulk implementation of the same thing.
    /// Undo runs them backwards, because a group can contain commands whose
    /// effects depend on order.
    /// </summary>
    private sealed class CompositeCommand : IEditorCommand
    {
        private readonly List<IEditorCommand> _steps;

        private CompositeCommand(List<IEditorCommand> steps) => _steps = steps;

        /// <summary>One command for one step, so a single-part edit still
        /// pushes exactly what it used to and nothing has to special-case a
        /// group of one.</summary>
        public static IEditorCommand Of(List<IEditorCommand> steps) =>
            steps.Count == 1 ? steps[0] : new CompositeCommand(steps);

        public void Execute()
        {
            foreach (var step in _steps) step.Execute();
        }

        public void Undo()
        {
            for (int i = _steps.Count - 1; i >= 0; i--) _steps[i].Undo();
        }
    }

    /// <summary>
    /// A move, remembered by the part's key rather than by its node (HP-05).
    ///
    /// The node is the wrong handle. Undoing a delete rebuilds the part as a new
    /// <c>Node3D</c>, so a move recorded before that delete was writing a
    /// position into an object Godot had already freed — which throws, and
    /// <see cref="EditorCommandHistory"/> then lost the history entry along with
    /// the exception. Nudge a group, delete it, let a frame pass, Ctrl+Z twice,
    /// and the second Z did nothing with no sign of why.
    /// </summary>
    private sealed class MoveCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly long _key;
        private readonly Vector3 _from;
        private readonly Vector3 _to;

        public MoveCommand(SceneEditor editor, long key, Vector3 from, Vector3 to)
        {
            _editor = editor;
            _key = key;
            _from = from;
            _to = to;
        }

        public void Execute() => MoveTo(_to);
        public void Undo() => MoveTo(_from);

        private void MoveTo(Vector3 at)
        {
            // A part that is genuinely gone is not an error: Clear drops the
            // history that referred to it, but an ordinary delete does not, and
            // undoing past a delete should skip the move rather than throw.
            if (_editor.FindPlaced(_key) is { } part && GodotObject.IsInstanceValid(part.Node))
                part.Node.Position = at;
        }
    }

    /// <summary>The part being relocated, still in the scene until the move lands.</summary>
    private PlacedPart? _movingPart;

    private SelectionGizmo _gizmo = null!;

    public override void _Ready()
    {
        _gizmo = new SelectionGizmo { Name = "SelectionGizmo" };
        AddChild(_gizmo);
    }

    private void SelectPartAt(Vector2 screenPosition)
    {
        var camera = GetViewport().GetCamera3D();
        if (camera is null) return;

        SelectPartAtRay(camera.ProjectRayOrigin(screenPosition),
                        camera.ProjectRayNormal(screenPosition));
    }

    /// <summary>
    /// Edit mode's click: select whatever the ray hits, or deselect. Refuses
    /// outright in Run mode (UX-44) rather than trusting every caller to
    /// check <see cref="Mode"/> first — <see cref="_UnhandledInput"/> already
    /// only reaches this in Edit mode, but a selection made through some
    /// other path while Run is active would be exactly the "a click selects
    /// in Edit and does not in Run" contract broken from the inside.
    /// </summary>
    /// <summary>
    /// The part a ray enters first, or null.
    ///
    /// Split out of <see cref="SelectPartAtRay"/> so a press can ask *what is
    /// under the cursor* without committing to selecting it — which is what
    /// Shift+click and "drag the group you already had" both need. The previous
    /// test ranked by camera distance to a part's origin and accepted anything
    /// within a metre, which cannot separate parts a cell apart on the same
    /// work plane, and made a 3 m belt clickable only near its middle.
    /// </summary>
    private PlacedPart? PickPartAtRay(Vector3 from, Vector3 dir)
    {
        float nearest = float.MaxValue;
        PlacedPart? hitPart = null;

        foreach (var entry in _placedParts)
        {
            if (PartBounds.RayDistance(entry.Node, from, dir) is not { } distance) continue;
            if (distance >= nearest) continue;

            nearest = distance;
            hitPart = entry;
        }

        return hitPart;
    }

    private PlacedPart? PickPartAt(Vector2 screenPosition)
    {
        if (Mode != EditorMode.Edit) return null;
        var camera = GetViewport()?.GetCamera3D();
        if (camera is null) return null;
        return PickPartAtRay(camera.ProjectRayOrigin(screenPosition),
                             camera.ProjectRayNormal(screenPosition));
    }

    public void SelectPartAtRay(Vector3 from, Vector3 dir)
    {
        if (Mode != EditorMode.Edit) return;

        PlacedPart? hitPart = PickPartAtRay(from, dir);

        if (hitPart is not null)
        {
            // Only when the selection actually changes: re-clicking the part
            // you already have selected is not a moment that needs teaching,
            // and a hint that reappears on every click is a nag (OP-09).
            bool isNew = _selectedPart != hitPart;
            SelectOnly(hitPart);
            if (isNew)
                IdleHint?.Announce($"Selected {hitPart.InstanceId}. Drag it to move it, " +
                                   "R to rotate, Ctrl+D to duplicate, Del to delete.");
        }
        else
        {
            DeselectPart();
        }
    }

    /// <summary>How a click reaches a part, or null if it is not operable by
    /// hand. Asked of the part (HP-34): this used to be two tables here — a
    /// dictionary from type name to its one clickable tag, and a set naming the
    /// types whose tag is analog — and a new operable part missing from either
    /// was a part the hover outline could not find or a click drove wrongly.
    /// </summary>
    private static PartOperation? OperationOf(PlacedPart part) =>
        part.Node is IPart p ? p.Operation : null;

    /// <summary>
    /// Run mode's click: find the operator control under the cursor and press
    /// it. Projects the screen position through the active camera, then hands
    /// off to <see cref="PressControlAtRay"/> -- kept separate so a headless
    /// self-test can drive the same dispatch with a synthetic ray and no
    /// camera at all.
    /// </summary>
    public void PressControlAt(Vector2 screenPosition)
    {
        var camera = GetViewport().GetCamera3D();
        if (camera is null) return;

        PressControlAtRay(camera.ProjectRayOrigin(screenPosition), camera.ProjectRayNormal(screenPosition));
    }

    /// <summary>
    /// While armed, a Run-mode click fails the drive it lands on instead of
    /// operating it — and a click on an already-failed drive clears it
    /// (FI-01).
    ///
    /// A mode rather than a modifier key, because the point is to be found.
    /// Faulting a machine is the one thing in this app a user would never
    /// discover by clicking around, and it is the half of PLC work the
    /// library could not teach until now: every actuator here did exactly what
    /// it was told, so a command and reality could never disagree, and an
    /// interlock exists precisely because the plant does not always obey.
    /// </summary>
    public bool FaultToolArmed { get; private set; }

    public void SetFaultToolArmed(bool armed)
    {
        FaultToolArmed = armed && Mode == EditorMode.Run;
        Toolbar?.ShowFaultTool(FaultToolArmed);
        // Forget what is currently outlined: arming changes both which parts
        // the outline can land on and what colour it means, and SetHoverTarget
        // short-circuits when the node has not changed.
        ClearHoverHighlight();
    }

    /// <summary>Which part the hover outline would land on for this ray, as an
    /// instance id — the same answer a click would act on, which is the whole
    /// contract the outline exists to keep. Null when a click there would do
    /// nothing.</summary>
    public string? HoverTargetAtRay(Vector3 from, Vector3 dir)
    {
        if (Mode != EditorMode.Run) return null;
        if (FaultToolArmed) return FindFaultTarget(from, dir)?.InstanceId;

        return FindOperableTarget(from, dir)?.Part.InstanceId;
    }

    /// <summary>Part types that have a drive that can fail. Derived from the
    /// tag set rather than listed twice: anything that declared a
    /// <c>.fault</c> tag can be faulted, and anything that did not, cannot.
    /// </summary>
    public bool CanFault(string partType) => PartCatalog.CanFault(partType);

    /// <summary>The nearest drive the ray lands on that could be failed.
    /// Shared by the click and the hover, for the same reason
    /// <see cref="FindOperableTarget"/> is: an outline that promises one thing
    /// while the click does another is worse than no outline.</summary>
    private PlacedPart? FindFaultTarget(Vector3 from, Vector3 dir)
    {
        float nearest = float.MaxValue;
        PlacedPart? hit = null;

        foreach (var entry in _placedParts)
        {
            if (!CanFault(entry.PartType)) continue;
            if (PartBounds.RayDistance(entry.Node, from, dir) is not { } distance) continue;
            if (distance >= nearest) continue;
            nearest = distance;
            hit = entry;
        }

        return hit;
    }

    /// <summary>Toggle the fault on whatever drive the ray lands on. Returns
    /// the instance id if one was toggled, so the hint bar can name it — a
    /// fault the user cannot see is a fault they will debug for an hour.
    /// </summary>
    public string? ToggleFaultAtRay(Vector3 from, Vector3 dir)
    {
        if (Mode != EditorMode.Run || Tags is null) return null;

        if (FindFaultTarget(from, dir) is not { } hit) return null;
        if (!hit.TagIds.TryGetValue("fault", out var id) || !Tags.Contains(id)) return null;

        // Forced, not Set: the fault is an Input, so a plain write would be
        // overwritten by whatever owns it next tick. Forcing is exactly the
        // right model anyway -- somebody is holding this contact closed, and
        // the Tag Inspector shows it held, and one click releases it.
        bool nowFaulted = !(Tags.TryGetVisible(id, out var current) && (bool)current);
        if (nowFaulted) Tags.Force(id, true);
        else Tags.ClearForce(id);

        return hit.InstanceId;
    }

    /// <summary>The pot currently being turned, if any. A drag owns the mouse
    /// until release, so nothing else in Run mode acts on the motion.</summary>
    private IDialPart? _dialDrag;

    public bool IsDraggingDial => _dialDrag is not null;

    private void FaultAt(Vector2 screenPosition)
    {
        var camera = GetViewport().GetCamera3D();
        if (camera is null) return;

        string? id = ToggleFaultAtRay(camera.ProjectRayOrigin(screenPosition),
                                      camera.ProjectRayNormal(screenPosition));
        IdleHint?.Announce(id is null
            ? "Nothing there has a drive that can fail. Click a conveyor or a pusher."
            : $"{id}.fault toggled. The command stays on; the machine stops obeying. "
              + "Click it again to clear, or release the force in the Tag Inspector.");
    }

    private bool BeginDialDragAt(Vector2 screenPosition)
    {
        var camera = GetViewport().GetCamera3D();
        if (camera is null) return false;
        return BeginDialDragAtRay(camera.ProjectRayOrigin(screenPosition),
                                  camera.ProjectRayNormal(screenPosition));
    }

    /// <summary>Grab the setpoint knob the ray lands on. Split from the screen
    /// entry point so a headless self-test can turn a real pot with a
    /// synthetic ray and no camera (OP-02).</summary>
    public bool BeginDialDragAtRay(Vector3 from, Vector3 dir)
    {
        if (Mode != EditorMode.Run) return false;

        if (FindOperableTarget(from, dir) is not { Region: PartOperate.DialRegion } hit)
            return false;
        if (hit.Part.Node is not IDialPart dial) return false;

        _dialDrag = dial;

        // Turning the knob takes the tag back from whoever forced it. A force
        // is sticky everywhere else in the editor and deliberately is not
        // here: the pot on the panel *is* the operator, and an operator who
        // turns a knob that then snaps back has been lied to.
        if (Tags is not null && hit.Part.TagIds.TryGetValue(dial.DialTagSuffix, out var id)
            && Tags.Contains(id))
            Tags.ClearForce(id);

        return true;
    }

    /// <summary>Screen pixels of travel since the last motion event, positive
    /// upward.</summary>
    public void DragDial(float pixelsUp) => _dialDrag?.TurnDial(pixelsUp);

    public void EndDialDrag() => _dialDrag = null;

    /// <summary>What a ray landed on: a part, and which of its regions
    /// (<paramref name="Region"/>) -- the empty string for a whole-body part,
    /// and <see cref="PartOperate.DialRegion"/> for a knob that is turned
    /// rather than pressed. Shared between the click dispatch
    /// (<see cref="PressControlAtRay"/>) and the hover highlight (UX-39), so
    /// the two can never disagree about what the cursor is over.</summary>
    private readonly record struct OperableHit(PlacedPart Part, string Region)
    {
        public Node3D Node => Part.Node;
    }

    /// <summary>
    /// Every part decides for itself what a click on it means (UX-37). Two
    /// tiers, both ray-tested and compared on the same nearest-wins footing,
    /// and which tier a part is in is the part's own answer
    /// (<see cref="PartOperation.Precise"/>) rather than a list here:
    ///
    /// * A <b>precise</b> part tests the ray against its own sub-regions -- a
    ///   panel's caps and pot, a stack light's lamps, a tank's valves, a
    ///   two-hand station's palms. A bounding box would cover the whole housing
    ///   and fire the nearest control no matter where on the part you clicked,
    ///   and for the two-hand station it would put both palms under one click,
    ///   which is precisely the defeat that part exists to refuse.
    /// * Everything else is tested against its whole bounding box, the same box
    ///   selection uses.
    ///
    /// Distances are measured along the ray in both tiers.
    /// Distance-to-object-centre would mix two metrics in one "nearest wins"
    /// comparison, which is exactly what let a wrong part shadow a stack
    /// light's own lamp during UX-37's own verification.
    /// </summary>
    private OperableHit? FindOperableTarget(Vector3 from, Vector3 dir)
    {
        float nearest = float.MaxValue;
        OperableHit? hit = null;

        foreach (var entry in _placedParts)
        {
            if (entry.Node is not IPart part) continue;
            if (part.Operation is not { } operation) continue;

            string region;
            float distance;

            if (operation.Precise)
            {
                if (part.HitTestRegion(from, dir) is not { } found) continue;
                region = found;
                distance = MeasureDistance(entry.Node, from, dir);
            }
            else
            {
                if (PartBounds.RayDistance(entry.Node, from, dir) is not { } boxDistance) continue;
                region = "";
                distance = boxDistance;
            }

            if (distance >= nearest) continue;
            nearest = distance;
            hit = new OperableHit(entry, region);
        }

        return hit;
    }

    /// <summary>Run mode's click, applied. Refuses outright in Edit mode
    /// (UX-44) rather than trusting every caller to check <see cref="Mode"/>
    /// first — the same defense-in-depth as <see cref="SelectPartAtRay"/>'s
    /// own guard, so "a control operates in Run and does not in Edit" holds
    /// regardless of what calls this.</summary>
    public void PressControlAtRay(Vector3 from, Vector3 dir)
    {
        if (Mode != EditorMode.Run) return;
        if (Tags is null) return;
        if (FindOperableTarget(from, dir) is not { } hit) return;

        // A press on the pot is a grab, not a button press — handled by the
        // drag path. Returning here rather than falling through is what stops
        // a click on the knob also firing whichever cap the enum happens to
        // default to.
        if (hit.Region == PartOperate.DialRegion) return;

        OperatePart(hit.Part, hit.Region);
    }

    /// <summary>Ray-parameter distance to a part, for comparing candidates of
    /// every operable type on one footing. A precise part's sub-region test
    /// (a cap, a lamp, a valve) already confirmed the ray is close enough to
    /// count as a hit; this answers "how far along the ray", the same
    /// question <see cref="PartBounds.RayDistance"/> answers for a whole-body
    /// part, by measuring against the part's own bounding box. Falls back to
    /// straight-line distance only if the box test itself somehow misses,
    /// which should not happen for a part the sub-region test already hit.</summary>
    private static float MeasureDistance(Node3D node, Vector3 from, Vector3 dir) =>
        PartBounds.RayDistance(node, from, dir) ?? from.DistanceTo(node.GlobalPosition);

    /// <summary>Run mode's hover: the same hit test a click would use, so the
    /// outline never promises a control the click itself would miss (UX-39).
    /// </summary>
    private void UpdateHoverHighlight(Vector2 screenPosition)
    {
        var camera = GetViewport().GetCamera3D();
        if (camera is null) { SetHoverTarget(null); return; }

        var from = camera.ProjectRayOrigin(screenPosition);
        var dir = camera.ProjectRayNormal(screenPosition);

        // With the fault tool armed, a click fails a drive rather than
        // operating anything — so the outline has to promise *that*, and in a
        // different colour. Highlighting the operable part under the cursor
        // while the click is going to break it is the same class of lie as an
        // outline over a part a click would miss (UX-39).
        if (FaultToolArmed)
        {
            var target = FindFaultTarget(from, dir);
            SetHoverTarget(target?.Node);
            SetDialCursor(false);
            return;
        }

        var hit = FindOperableTarget(from, dir);
        SetHoverTarget(hit?.Node);

        // The outline says "this part responds to a click", which is the wrong
        // promise for the one control that responds to a *drag*. A cursor that
        // changes shape is how every other application says "grab this and
        // pull", and without it the pot is a control you have to already know
        // about to find (OP-02).
        SetDialCursor(hit is { Region: PartOperate.DialRegion });
    }

    private bool _dialCursor;

    private void SetDialCursor(bool over)
    {
        if (over == _dialCursor) return;
        _dialCursor = over;
        Input.SetDefaultCursorShape(over ? Input.CursorShape.Vsize : Input.CursorShape.Arrow);
    }

    private void SetHoverTarget(Node3D? node)
    {
        if (node == _hoveredNode) return;
        _hoveredNode = node;

        if (node is null) { ClearHoverHighlight(); return; }

        _hoverOutline ??= BuildHoverOutline();
        if (_hoverOutline.GetParent() is null) AddChild(_hoverOutline);

        if (_hoverOutline.MaterialOverride is StandardMaterial3D hoverMat)
        {
            hoverMat.AlbedoColor = FaultToolArmed
                ? new Color(1.0f, 0.25f, 0.20f, 0.38f)    // this click breaks it
                : new Color(1.0f, 0.85f, 0.20f, 0.35f);   // this click operates it
        }

        var box = PartBounds.Measure(node);
        _hoverOutline.Mesh = new BoxMesh { Size = box.Size * 1.08f };
        // World-space placement rather than reparenting under the hovered
        // node: a part can be deleted while still hovered (a scene reload
        // triggered from the toolbar, say), which would free a reparented
        // outline right along with it.
        _hoverOutline.GlobalTransform = node.GlobalTransform *
            new Transform3D(Basis.Identity, box.Position + box.Size / 2);
        _hoverOutline.Visible = true;
    }

    /// <summary>Hide the outline without necessarily forgetting it exists --
    /// called on every mode switch away from Run and every scene wipe, so a
    /// deleted or reloaded part never leaves a highlight floating over empty
    /// space.</summary>
    private void ClearHoverHighlight()
    {
        _hoveredNode = null;
        if (_hoverOutline is not null) _hoverOutline.Visible = false;
        // Called on every mode switch away from Run and every scene wipe, so
        // the grab cursor goes with it — a resize arrow left over the Build
        // palette would be a cursor lying about what a click does.
        SetDialCursor(false);
    }

    private static MeshInstance3D BuildHoverOutline() => new()
    {
        Name = "RunModeHoverOutline",
        Visible = false,
        MaterialOverride = new StandardMaterial3D
        {
            AlbedoColor = new Color(1.0f, 0.85f, 0.2f, 0.35f),
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
        },
    };

    /// <summary>Whether any part in the scene carries a knob worth telling the
    /// user about — one whose scale plate spans a real range. A pot with
    /// min == max cannot be turned and is not worth naming. Asked of the part
    /// through <see cref="IDialPart"/>, so a second turnable control would be
    /// found here without this method learning what it is.</summary>
    public bool HasTurnablePot()
    {
        foreach (var entry in _placedParts)
        {
            if (entry.Node is IDialPart dial && dial.DialTurnable) return true;
        }
        return false;
    }

    /// <summary>How many parts in the scene would respond to a click in Run
    /// mode, and a short list naming what kinds -- the entering-Run hint
    /// (UX-39) uses this so a scene built with nothing operable says that
    /// plainly instead of presenting a mode that silently does nothing.
    ///
    /// Both halves come from the part's own <see cref="PartOperation"/>. They
    /// used to come from a membership test against one table and a
    /// twenty-branch switch producing the noun, and a part missing from either
    /// was a part this banner never mentioned -- the surest way to leave a
    /// control undiscovered.
    /// </summary>
    public (int Count, string Kinds) DescribeOperableParts()
    {
        var kinds = new List<string>();
        int count = 0;

        foreach (var entry in _placedParts)
        {
            if (OperationOf(entry) is not { } operation) continue;

            count++;
            if (!kinds.Contains(operation.Kind)) kinds.Add(operation.Kind);
        }

        return (count, string.Join(", ", kinds));
    }

    /// <summary>Apply a click's effect once <see cref="PressControlAtRay"/> has
    /// picked a part and, for a precise part, which of its regions was hit.
    ///
    /// The part decides what the click means (HP-34). This used to be a switch
    /// on the type name with five special cases and a default that consulted
    /// two more tables -- and every one of those cases exists because a click
    /// on a real machine is not uniformly "flip the bit": a valve opens fully,
    /// an emitter pulses, a selector steps round a detent, a guard door slides
    /// or refuses, a fan needs an enable *and* a reference. Those are facts
    /// about the machines, and they are now written where the machines are.
    ///
    /// Every write still goes through <see cref="TagTable.Force"/>, the same
    /// call the Tag Inspector and the property panel (UX-34) make, so a part
    /// operated by hand stays sticky exactly like they do (§5.2).</summary>
    private void OperatePart(PlacedPart entry, string region)
    {
        if (entry.Node is not IPart part) return;
        part.Operate(new PartOperate(Tags, entry.TagIds, entry.InstanceId, region, PulseTag));
    }

    /// <summary>Raise a tag and release it a moment later, for a control that
    /// means a rising edge rather than a level. The timer belongs to the scene
    /// tree, which a part has no business reaching into.</summary>
    private void PulseTag(string id)
    {
        Tags.Force(id, true);
        GetTree().CreateTimer(0.05).Timeout += () => { if (Tags.Contains(id)) Tags.ClearForce(id); };
    }

    /// <summary>
    /// Give a part a name you would willingly write into a PLC program.
    ///
    /// Fails, with a reason, rather than half-succeeding: an id already in use
    /// would collide on the tag table, and a part that only *views* tags the
    /// simulation owns cannot be renamed at all — <see cref="SortingScene"/>
    /// writes <c>conveyor.rotate</c> by that exact name every tick, so moving
    /// the tag would leave the scene talking to nothing.
    /// </summary>
    public bool TryRenameSelectedPart(string newId, out string problem)
    {
        if (_selectedPart is not { } selected) { problem = "nothing selected"; return false; }
        return TryRenamePart(selected.InstanceId, newId, out problem);
    }

    /// <summary>Rename by id, so the rules can be exercised without a mouse.</summary>
    public bool TryRenamePart(string instanceId, string newId, out string problem)
    {
        problem = "";
        if (Tags is null) { problem = "no tag table"; return false; }

        int index = _placedParts.FindIndex(p => p.InstanceId == instanceId);
        if (index < 0) { problem = $"no part called '{instanceId}'"; return false; }
        var entry = _placedParts[index];

        newId = newId.Trim();
        if (newId == entry.InstanceId) return true;

        if (!PartTagManager.IsValidInstanceId(newId))
        {
            problem = "use letters, digits and underscores — no dots or spaces";
            return false;
        }
        if (!entry.OwnsTags)
        {
            problem = "this part mirrors tags the simulation owns and cannot be renamed";
            return false;
        }
        if (PartTagManager.HasTagsFor(newId, Tags))
        {
            problem = $"'{newId}' is already taken";
            return false;
        }
        if (!PartTagManager.RenameInstance(entry.InstanceId, newId, Tags))
        {
            problem = "rename rejected by the tag table";
            return false;
        }

        // A typed id is a claimed id: auto-numbering has to step over it, or the
        // next placement of this type mints the name somebody just chose and
        // adopts its tags (HP-15).
        PartTagManager.NoteInstanceId(entry.PartType, newId);

        // Anything the part holds that names one of its own tags has to follow
        // it, or it points at a tag that no longer exists. Which settings those
        // are is the part's own answer (HP-34): this used to reach into a
        // remover by name and clear a panel's pulse queue out of a dictionary
        // here, and a third part with the same problem would have needed a
        // third clause nobody would have thought to add.
        if (entry.Node is IPart renaming) renaming.PrefixRenamed(entry.InstanceId, newId);

        var renamed = entry with { InstanceId = newId };
        renamed.InvalidateTagIds();   // `with` copies the old id cache too
        _placedParts[index] = renamed;
        if (_selectedPart == entry)
        {
            SelectOnly(renamed);
            PartNameLabel.Apply(renamed.Node, newId, PartNamesVisible);
            // Rebuild the inspector so its name field and its idea of the
            // "previous" name both move on. Without this a second rename in a
            // row would restore the *original* id if it were rejected.
            PropertyInspector?.InspectNode(renamed.Node, newId, renamed.PartType);
        }

        MarkDirty();
        NotifyTagsChanged();
        GD.Print($"Renamed '{entry.InstanceId}' to '{newId}'");
        return true;
    }

    /// <summary>Select a part by id and show it in the inspector — what a click
    /// does, without needing a camera to click through.</summary>
    public bool SelectPartForInspection(string instanceId)
    {
        int index = _placedParts.FindIndex(p => p.InstanceId == instanceId);
        if (index < 0) return false;

        SelectOnly(_placedParts[index]);
        return true;
    }

    /// <summary>Instance ids currently in the scene, for tests and tooling.</summary>
    public IReadOnlyList<string> PlacedPartIds()
    {
        var ids = new List<string>();
        foreach (var part in _placedParts) ids.Add(part.InstanceId);
        return ids;
    }

    /// <summary>
    /// What the camera should frame when the user presses F (CP-16): the
    /// selected part if there is one, otherwise everything placed.
    ///
    /// In world space, and measured from the meshes rather than assumed from
    /// the origins — a part's origin is on the work plane and its geometry
    /// hangs off it in whatever direction that part needs, so framing origins
    /// would aim the camera at a point above the chute and below the stack
    /// light. Null when there is nothing placed at all, which the caller
    /// should treat as "leave the camera alone" rather than as an empty box at
    /// the origin.
    /// </summary>
    public Aabb? FocusBounds()
    {
        if (_selectedPart is { } selected) return WorldBounds(selected.Node);

        Aabb? all = null;
        foreach (var part in _placedParts)
        {
            var box = WorldBounds(part.Node);
            all = all is { } acc ? acc.Merge(box) : box;
        }
        return all;
    }

    private static Aabb WorldBounds(Node3D node)
    {
        var local = PartBounds.Measure(node);
        var xform = node.GlobalTransform;

        // Transform all eight corners, not just position and end: a rotated
        // part's box is not axis-aligned in world space, and taking two corners
        // through the transform would give a box that misses half of it.
        var bounds = new Aabb(xform * local.GetEndpoint(0), Vector3.Zero);
        for (int corner = 1; corner < 8; corner++)
            bounds = bounds.Expand(xform * local.GetEndpoint(corner));
        return bounds;
    }

    /// <summary>The node behind a placed part, for tests and tooling. A test
    /// that can only reach a part through its tags cannot tell a part that
    /// reports the right number from one that reports it while its geometry
    /// says something else — which is the failure mode the roller deck's
    /// tumbling axis had, and the reason CP-31 asks parts about themselves as
    /// well as about their tags.</summary>
    public Node3D? NodeFor(string instanceId)
    {
        int index = _placedParts.FindIndex(p => p.InstanceId == instanceId);
        return index < 0 ? null : _placedParts[index].Node;
    }

    /// <summary>A placed part's position, for tests and tooling — verifying a
    /// drag-to-move (OP-08) without a mouse.</summary>
    public Vector3? PositionOf(string instanceId)
    {
        int index = _placedParts.FindIndex(p => p.InstanceId == instanceId);
        return index < 0 ? null : _placedParts[index].Node.Position;
    }

    /// <summary>A placed part's Y rotation in radians, for tests and tooling
    /// — verifying FF-20's rotate-in-place without a mouse.</summary>
    public float? RotationYOf(string instanceId)
    {
        int index = _placedParts.FindIndex(p => p.InstanceId == instanceId);
        return index < 0 ? null : _placedParts[index].Node.Rotation.Y;
    }

    private void DeselectPart()
    {
        _selection.Clear();
        _selectedPart = null;
        _gizmo?.AttachToNodes(System.Array.Empty<Node3D>());
        PropertyInspector?.InspectNode(null, "", "");
        EmitSignal(SignalName.SelectionChanged, 0);
    }

    /// <summary>Replace the selection with one part.</summary>
    private void SelectOnly(PlacedPart part)
    {
        _selection.Clear();
        _selection.Add(part);
        RefreshSelection();
    }

    /// <summary>Add a part to the selection, or take it out if it is already
    /// in — what Shift+click does. Removing the primary promotes whatever is
    /// left, so the inspector never ends up describing a part that is no longer
    /// selected.</summary>
    private void ToggleSelection(PlacedPart part)
    {
        if (!_selection.Remove(part)) _selection.Add(part);
        if (_selection.Count == 0) { DeselectPart(); return; }
        RefreshSelection();
    }

    private void SelectAll(IEnumerable<PlacedPart> parts, bool add)
    {
        if (!add) _selection.Clear();
        foreach (var part in parts)
        {
            if (!_selection.Contains(part)) _selection.Add(part);
        }
        if (_selection.Count == 0) { DeselectPart(); return; }
        RefreshSelection();
    }

    /// <summary>Push the selection out to everything that shows it. One place,
    /// so the gizmo, the inspector and the primary cannot disagree.</summary>
    private void RefreshSelection()
    {
        _selectedPart = _selection.Count > 0 ? _selection[^1] : null;

        var nodes = new List<Node3D>(_selection.Count);
        foreach (var part in _selection) nodes.Add(part.Node);
        _gizmo?.AttachToNodes(nodes);

        // One part gets its own panel; several get the primary's, because a
        // panel that tried to edit five parts at once would have to invent a
        // meaning for five different belt speeds.
        if (_selectedPart is { } primary)
            PropertyInspector?.InspectNode(primary.Node, primary.InstanceId, primary.PartType);

        EmitSignal(SignalName.SelectionChanged, _selection.Count);
    }

    /// <summary>How many parts are selected. The toolbar says so when it is
    /// more than one — a group operation with nothing on screen to say how
    /// large the group is is a group operation nobody trusts.</summary>
    public int SelectionCount => _selection.Count;

    /// <summary>
    /// Are part names floating over the scene? (NV-01)
    ///
    /// Off by default, because a finished line with thirty names over it is
    /// harder to look at than one without — but on demand, because the instance
    /// id is the tag prefix and it is the one thing you need the moment you
    /// stop building and start writing a program against what you built.
    /// </summary>
    public bool PartNamesVisible { get; private set; }

    public void SetPartNamesVisible(bool visible)
    {
        if (visible == PartNamesVisible) return;
        PartNamesVisible = visible;

        foreach (var part in _placedParts) PartNameLabel.SetVisible(part.Node, visible);
        GD.Print(visible ? "Part names on" : "Part names off");
    }

    public void TogglePartNames() => SetPartNamesVisible(!PartNamesVisible);

    /// <summary>Select every part in the scene — Ctrl+A. Cheap, expected, and
    /// the fastest way to reach "move the whole line two cells over".</summary>
    public void SelectEverything()
    {
        if (Mode != EditorMode.Edit || _placedParts.Count == 0) return;
        SelectAll(_placedParts, add: false);
        GD.Print($"Selected all {_selection.Count} parts");
    }

    /// <summary>
    /// What Ctrl+C holds (NV-02).
    ///
    /// Stored as <see cref="PartInstanceData"/> — type, offset from the
    /// group's own anchor, rotation and captured properties — rather than as
    /// references to the parts themselves, so a copy survives the originals
    /// being deleted, and so it can be pasted into a *different scene*, which
    /// is the reason to have a clipboard at all rather than only Ctrl+D.
    ///
    /// Ids are deliberately not kept. A pasted part is a new part and must mint
    /// a fresh instance id, or it would adopt the tags of whatever it was
    /// copied from and two parts would drive one belt.
    /// </summary>
    private readonly List<PartInstanceData> _clipboard = new();

    public int ClipboardCount => _clipboard.Count;

    public void CopySelection()
    {
        if (_selection.Count == 0) return;

        // Everything is stored relative to the first selected part, so a paste
        // can put the group down anywhere and keep its shape.
        Vector3 anchor = _selection[0].Node.Position;

        _clipboard.Clear();
        foreach (var entry in _selection)
        {
            Vector3 offset = entry.Node.Position - anchor;
            _clipboard.Add(new PartInstanceData
            {
                Id = "",
                Type = entry.PartType,
                Position = new[] { offset.X, offset.Y, offset.Z },
                Rotation = new[]
                {
                    entry.Node.Rotation.X, entry.Node.Rotation.Y, entry.Node.Rotation.Z,
                },
                // ForCopy, not Capture: the ids are reset here for exactly this
                // reason, and a setting *naming another tag* is the same problem
                // one level down (HP-16).
                Properties = PartProperties.CaptureForCopy(entry.Node),
            });
        }

        _pasteAnchor = anchor;
        _pasteOffset = Vector3.Zero;
        GD.Print($"Copied {_clipboard.Count} part(s)");
    }

    /// <summary>
    /// Put the clipboard down, one cell clear of where it came from, as one
    /// undo step — and select what landed, so a second Ctrl+V walks on the way
    /// a second Ctrl+D does.
    /// </summary>
    public void PasteClipboard()
    {
        if (Mode != EditorMode.Edit || _clipboard.Count == 0) return;

        float cell = Grid?.CellSize ?? 0.5f;
        // Offset from the *last paste* rather than always from the original, so
        // repeated pastes lay a row out instead of stacking in one cell.
        _pasteOffset += new Vector3(cell * 2.0f, 0, 0);

        var copies = new List<PartInstanceData>(_clipboard.Count);
        foreach (var item in _clipboard)
        {
            copies.Add(new PartInstanceData
            {
                Id = "",
                Type = item.Type,
                Position = new[]
                {
                    _pasteAnchor.X + item.Position[0] + _pasteOffset.X,
                    PartLayout.WorkPlaneY,
                    _pasteAnchor.Z + item.Position[2] + _pasteOffset.Z,
                },
                Rotation = item.Rotation,
                Properties = item.Properties,
            });
        }

        _history.ExecuteCommand(new DuplicateGroupCommand(this, copies));
        MarkDirty();
        GD.Print($"Pasted {copies.Count} part(s)");
    }

    /// <summary>Where the clipboard was cut from, and how far the last paste
    /// stepped away from it.</summary>
    private Vector3 _pasteAnchor;
    private Vector3 _pasteOffset;

    /// <summary>Instance ids of everything selected, for tests and for the
    /// status line.</summary>
    public IReadOnlyList<string> SelectedInstanceIds()
    {
        var ids = new List<string>(_selection.Count);
        foreach (var part in _selection) ids.Add(part.InstanceId);
        return ids;
    }

    /// <summary>Delete everything selected, as one undo step. Public because
    /// Del is not the only route to it: the toolbar offers it, and a headless
    /// test drives it directly.</summary>
    public void DeleteSelectedPart()
    {
        if (_selection.Count == 0) return;

        var doomed = new List<IEditorCommand>(_selection.Count);
        foreach (var entry in _selection)
        {
            // The whole part, settings included. Ctrl+Z has to return what was
            // deleted, not another part of the same type standing in the same
            // cell (HP-03).
            doomed.Add(new PartCommand(this, Snapshot(entry), isPlacement: false, key: entry.Key));
        }

        GD.Print(doomed.Count == 1
            ? $"Deleted part '{_selection[0].InstanceId}'"
            : $"Deleted {doomed.Count} parts (Ctrl+Z to put them back)");

        DeselectPart();
        // One step, so Ctrl+Z after deleting a section of line brings the whole
        // section back rather than one belt per press.
        _history.ExecuteCommand(CompositeCommand.Of(doomed));
        MarkDirty();
        NotifyTagsChanged();
    }

    /// <summary>
    /// Empty the world: every part gone, and the sorting line's engine-declared
    /// tags with them.
    ///
    /// <see cref="ClearAllPlacedParts"/> alone cannot do this. Those ten tags
    /// are declared at startup rather than owned by a part, so clearing the
    /// parts leaves them in the table and the next scene inherits a conveyor and
    /// two box counters it does not have. Left alone when a
    /// <see cref="SortingScene"/> is running, because that owns them.
    /// </summary>
    public void NewEmptyScene()
    {
        ClearAllPlacedParts();
        if (Scene is null && Tags is not null) SortingTags.Undeclare(Tags);

        SceneName = "untitled";
        IsDirty = false;
        NotifyTagsChanged();
        GD.Print("New empty scene");
    }

    /// <summary>Rebuild the sorting line the engine ships with.</summary>
    public void LoadDefaultSortingScene()
    {
        ClearAllPlacedParts();
        if (Scene is null && Tags is not null)
        {
            SortingTags.Undeclare(Tags);
            SortingTags.Declare(Tags);
        }

        SceneName = "sorting-by-height";
        RegisterDefaultSceneParts(physical: Scene is null);
        IsDirty = false;
        NotifyTagsChanged();
    }

    /// <summary>
    /// Start from a shipped template. Same as loading any scene file, except the
    /// sorting line's tags are dropped first so a template starts from a clean
    /// I/O list rather than inheriting the demo's.
    /// </summary>
    /// <param name="path">The template to open.</param>
    /// <remarks>The undeclare used to happen here, before the load was
    /// attempted — the same destroy-before-validating shape as HP-02, one level
    /// up. A template that could not be opened left the sorting demo's tags
    /// gone and the scene that was running with nothing declaring them.</remarks>
    public bool LoadTemplate(string path) => LoadSceneFromFile(path, dropSortingTags: true);

    /// <summary>
    /// Write the scene to disk. Returns false if it did not land.
    ///
    /// It used to return nothing and report success unconditionally: the open
    /// was <c>file?.StoreString(json)</c>, so a path that could not be opened at
    /// all took the null-conditional branch and fell straight through to
    /// <c>IsDirty = false</c> and "Saved scene to …". A read-only directory, a
    /// removed USB stick or a full disk all printed the same cheerful line, and
    /// the title bar stopped saying there was anything unsaved — which is the
    /// one signal a person has that their afternoon is still only in memory
    /// (HP-01).
    ///
    /// On failure <see cref="IsDirty"/> is left alone and
    /// <see cref="SaveFailed"/> is raised, because the console is not where
    /// somebody who just pressed Save is looking.
    /// </summary>
    public bool SaveSceneToFile(string path = "user://custom_scene.json")
    {
        // Name the scene after the file it lives in, so saving as "palletiser"
        // makes the bus report "palletiser" rather than every scene claiming to
        // be the sorting demo.
        string stem = System.IO.Path.GetFileNameWithoutExtension(path);
        if (stem.Length > 0 && stem != "custom_scene") SceneName = stem;

        var data = new SceneData { Name = SceneName, Parts = CapturePartsSnapshot() };
        string json = data.ToJson();

        // Write somewhere else first (HP-47). Opening the destination for
        // writing *truncates it*, so the old code destroyed the last good scene
        // file before it had written a single byte of the new one: a crash, a
        // full disk or a drive that goes away halfway through left a truncated
        // file where a working scene used to be, and that file was usually the
        // only copy. Nothing touches the destination until a complete, verified
        // file exists beside it.
        string partial = path + ".part";

        var file = Godot.FileAccess.Open(partial, Godot.FileAccess.ModeFlags.Write);
        if (file is null)
            return SaveDidNotLand(path, $"could not open {partial} for writing " +
                                        $"({Godot.FileAccess.GetOpenError()})");

        file.StoreString(json);
        // Before Close(), because Close() clears the file's error state — and
        // after StoreString, because that is the call that can fail on a full
        // disk or a drive that has gone away mid-write.
        var wrote = file.GetError();
        file.Close();

        if (wrote != Error.Ok)
        {
            Discard(partial);
            return SaveDidNotLand(path, $"the write failed ({wrote})");
        }

        // Read it back before believing it. A short write is what a disk that
        // filled up during the save looks like, and on some filesystems it does
        // not raise an error at all — the bytes simply are not there.
        using (var check = Godot.FileAccess.Open(partial, Godot.FileAccess.ModeFlags.Read))
        {
            if (check is null)
            {
                Discard(partial);
                return SaveDidNotLand(path, "the file it wrote could not be read back " +
                                            $"({Godot.FileAccess.GetOpenError()})");
            }

            string readBack = check.GetAsText();
            if (readBack != json)
            {
                Discard(partial);
                return SaveDidNotLand(path,
                    $"only {readBack.Length} of {json.Length} characters reached the disk");
            }
        }

        var moved = Godot.DirAccess.RenameAbsolute(partial, path);
        if (moved != Error.Ok)
        {
            // The destination is still whatever it was. Say so plainly: "could
            // not replace" and "could not write" are different problems and
            // send you to different places.
            Discard(partial);
            return SaveDidNotLand(path, $"the finished file could not be moved into place ({moved}); " +
                                        "the previous scene file is untouched");
        }

        IsDirty = false;
        GD.Print($"Saved scene to {path} ({_placedParts.Count} parts)");
        return true;
    }

    /// <summary>Drop a half-written file rather than leaving it beside the real
    /// one, where the next person to look at the directory has to guess which of
    /// the two is their scene.</summary>
    private static void Discard(string partial)
    {
        if (Godot.FileAccess.FileExists(partial)) Godot.DirAccess.RemoveAbsolute(partial);
    }

    /// <summary>Report a save that did not happen. <see cref="IsDirty"/> is
    /// deliberately untouched: the work is still unsaved and the title has to
    /// keep saying so.</summary>
    private bool SaveDidNotLand(string path, string why)
    {
        GD.PushError($"Save failed: {path} — {why}");
        GD.PrintErr($"Could not save scene to {path}: {why}");
        EmitSignal(SignalName.SaveFailed, path, why);
        return false;
    }

    /// <summary>
    /// Open a scene file. Returns false without touching the open scene if the
    /// file cannot be used.
    ///
    /// The order here is the whole of HP-02. It used to be: clear the scene,
    /// then open the file, then parse it — so every way a file could be bad cost
    /// the user the scene they already had, and they found out by watching their
    /// work disappear. Read, parse, validate, and only then clear.
    /// </summary>
    /// <param name="dropSortingTags">Undeclare the sorting demo's engine-owned
    /// tags first, so a template starts from a clean I/O list rather than
    /// inheriting the demo's. Done on the far side of validation, with the
    /// clear, because it is just as destructive.</param>
    public bool LoadSceneFromFile(string path = "user://custom_scene.json",
                                  bool dropSortingTags = false)
    {
        if (!Godot.FileAccess.FileExists(path))
        {
            GD.Print($"No saved scene file found at {path}");
            return LoadRefused(path, "there is no file there");
        }

        string json;
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Read))
        {
            if (file is null)
                return LoadRefused(path, $"it could not be opened ({Godot.FileAccess.GetOpenError()})");
            json = file.GetAsText();
        }

        if (!SceneData.TryParse(json, out var data, out string problem) || data is null)
            return LoadRefused(path, problem);

        // Part types this build does not have are dropped rather than refused,
        // and *said out loud* rather than dropped silently — which is what
        // happened before, because CreatePartNode returns null for an unknown
        // type and SpawnFromData quietly returns null in turn. A scene from a
        // newer build that added a part keeps its version number, so refusing
        // the file would make every such scene unopenable; but a machine
        // vanishing from somebody's line with no message is the same silent
        // loss this whole phase is about.
        var unknown = new List<string>();
        foreach (var part in data.Parts)
        {
            if (!PartCatalog.IsKnownType(part.Type) && !unknown.Contains(part.Type))
                unknown.Add(part.Type);
        }

        // Past this line the open scene is gone. Everything that could refuse
        // the file has already had its turn.
        if (dropSortingTags && Scene is null && Tags is not null) SortingTags.Undeclare(Tags);
        ClearAllPlacedParts();

        if (data.Name is { Length: > 0 }) SceneName = data.Name;

        // Before AddChild inside RestorePartsFromSnapshot: parts build their
        // geometry from position/rotation/properties in _Ready, so applying
        // them afterwards would leave the mesh showing the old configuration.
        RestorePartsFromSnapshot(data.Parts);

        IsDirty = false;
        GD.Print($"Loaded scene from {path} ({_placedParts.Count} parts)");

        if (unknown.Count > 0)
        {
            string types = string.Join(", ", unknown);
            GD.PrintErr($"Scene {path} contains part types this build does not have: {types}. " +
                        "They were left out. Saving over the file would lose them.");
            EmitSignal(SignalName.SceneLoadIncomplete, path, types);
        }

        // Deferred: the parts were added this frame and have not run _Ready, so
        // their geometry does not exist yet and anything measuring them now
        // would frame a set of empty boxes at their origins.
        CallDeferred(nameof(AnnounceSceneLoaded));
        return true;
    }

    /// <summary>Refuse a scene file, leaving the open scene exactly as it was.
    /// Reported through a signal as well as the console for the same reason a
    /// failed save is: the person is looking at the window.</summary>
    private bool LoadRefused(string path, string why)
    {
        GD.PrintErr($"Could not open scene {path}: {why}");
        EmitSignal(SignalName.LoadFailed, path, why);
        return false;
    }

    private void AnnounceSceneLoaded() => EmitSignal(SignalName.SceneLoaded);

    public void ClearAllPlacedParts()
    {
        ClearPlacedPartsCore();
        _history.Clear();   // its commands refer to parts that are now gone
        GD.Print("Cleared all editor placed parts");
    }

    /// <summary>The wipe itself, with no opinion on history. Shared by the
    /// scene-loading paths (which drop history entirely — it refers to parts
    /// this just freed) and <see cref="ClearSceneCommand"/> (which needs the
    /// wipe to be redoable without also being the thing that erases itself).
    /// </summary>
    private void ClearPlacedPartsCore()
    {
        ClearHoverHighlight();
        foreach (var part in _placedParts)
        {
            if (part.OwnsTags && Tags is not null)
                PartTagManager.UnregisterPartTags(part.InstanceId, Tags);
            part.Node.QueueFree();
        }
        _placedParts.Clear();
        PartTagManager.ResetCounters();
        _movingPart = null;
        DeselectPart();
        NotifyTagsChanged();
    }

    /// <summary>Everything needed to rebuild the current parts exactly as they
    /// stand — the same shape <see cref="SaveSceneToFile"/> writes to disk,
    /// kept in memory instead so Clear can be undone without a file.</summary>
    private List<PartInstanceData> CapturePartsSnapshot()
    {
        var list = new List<PartInstanceData>();
        foreach (var part in _placedParts)
        {
            list.Add(new PartInstanceData
            {
                Id = part.InstanceId,
                Type = part.PartType,
                Position = new float[] { part.Node.Position.X, part.Node.Position.Y, part.Node.Position.Z },
                Rotation = new float[] { part.Node.Rotation.X, part.Node.Rotation.Y, part.Node.Rotation.Z },
                Properties = PartProperties.Capture(part.Node),
            });
        }
        return list;
    }

    /// <summary>Rebuild parts from a snapshot, preserving their ids so wiring
    /// and mappings survive. Shared by scene loading and Clear's undo.</summary>
    private void RestorePartsFromSnapshot(List<PartInstanceData> parts)
    {
        foreach (var p in parts) SpawnFromData(p, notify: false);
        NotifyTagsChanged();
    }

    /// <summary>
    /// Build, parent and register one part from captured data — position,
    /// rotation, type and every property PartProperties knows how to apply.
    /// The single-part building block <see cref="RestorePartsFromSnapshot"/>
    /// and <see cref="DuplicateSelectedPart"/> both reduce to: the whole
    /// difference between "rebuild ten parts" and "paste one part with its
    /// settings intact" is how many <see cref="PartInstanceData"/> you hand it.
    /// </summary>
    /// <param name="reviveKey">The part key to restore, when this respawn is an
    /// undo of a delete rather than a new part. Zero mints a fresh one. See
    /// <see cref="_nextPartKey"/>.</param>
    private PlacedPart? SpawnFromData(PartInstanceData p, bool notify = true, long reviveKey = 0)
    {
        var node = CreatePartNode(p.Type);
        if (node is null) return null;

        node.Position = new Vector3(p.Position[0], p.Position[1], p.Position[2]);
        node.Rotation = new Vector3(p.Rotation[0], p.Rotation[1], p.Rotation[2]);
        PartProperties.Apply(node, p.Properties);
        GetParent()?.AddChild(node);

        var (instanceId, owns) = PartTagManager.RegisterPartTags(node, p.Type, Tags, p.Id);
        var placed = new PlacedPart(node, instanceId, p.Type, owns,
                                    reviveKey != 0 ? reviveKey : NextPartKey());
        _placedParts.Add(placed);
        // The name goes on here rather than in a pass afterwards, so a scene
        // loaded with names already on comes up labelled instead of needing
        // the toggle flicked to catch up (NV-01).
        PartNameLabel.Apply(node, instanceId, PartNamesVisible);
        if (notify) NotifyTagsChanged();
        return placed;
    }

    /// <summary>Duplicate the selected part one grid cell over, with its
    /// current properties, as a single undoable placement. See FF-21.</summary>
    public void DuplicateSelectedPart()
    {
        if (_selection.Count == 0) return;
        if (_selection.Count > 1) { DuplicateSelection(); return; }

        var source = _selection[0];
        var offset = source.Node.Position + DuplicateOffset(source.Node);
        var data = new PartInstanceData
        {
            // Empty, not "part": RegisterPartTags only auto-numbers a fresh id
            // when preferredId is empty. A literal non-empty placeholder here
            // would make every duplicate "adopt" the first one's tags instead
            // of getting its own — same bug a duplicate id from a file guards
            // against, self-inflicted.
            Id = "",
            Type = source.PartType,
            Position = new[] { offset.X, offset.Y, offset.Z },
            Rotation = new[] { source.Node.Rotation.X, source.Node.Rotation.Y, source.Node.Rotation.Z },
            Properties = PartProperties.CaptureForCopy(source.Node),
        };

        _history.ExecuteCommand(new DuplicateCommand(this, data));
        MarkDirty();
        GD.Print($"Duplicated '{source.InstanceId}'");
    }

    /// <summary>
    /// Copy a whole selection, shifted by one offset (ES-03).
    ///
    /// One offset for the group rather than each part's own, because a group is
    /// a *shape* — duplicating a belt, its sensor and its pusher has to keep
    /// them lined up with each other, and giving each one its own footprint
    /// offset would take the copy apart. The offset is the widest part's, so
    /// the copy clears the original along the axis the group was built on.
    /// </summary>
    private void DuplicateSelection()
    {
        Vector3 offset = Vector3.Zero;
        foreach (var entry in _selection)
        {
            Vector3 own = DuplicateOffset(entry.Node);
            if (own.Length() > offset.Length()) offset = own;
        }

        var copies = new List<PartInstanceData>(_selection.Count);
        foreach (var entry in _selection)
        {
            Vector3 to = entry.Node.Position + offset;
            copies.Add(new PartInstanceData
            {
                Id = "",
                Type = entry.PartType,
                Position = new[] { to.X, to.Y, to.Z },
                Rotation = new[]
                {
                    entry.Node.Rotation.X, entry.Node.Rotation.Y, entry.Node.Rotation.Z,
                },
                Properties = PartProperties.CaptureForCopy(entry.Node),
            });
        }

        _history.ExecuteCommand(new DuplicateGroupCommand(this, copies));
        MarkDirty();
        GD.Print($"Duplicated {copies.Count} parts");
    }

    /// <summary>
    /// The group half of <see cref="DuplicateCommand"/>, and the one the first
    /// draft of HP-20 missed. Ctrl+V goes through here too, so without the
    /// captured identities a paste minted a fresh set of ids on every redo — a
    /// whole section of line renaming itself behind a connected driver.
    /// </summary>
    private sealed class DuplicateGroupCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly List<PartInstanceData> _data;

        /// <summary>One key per entry of <see cref="_data"/>, by index, so an
        /// item whose type could not be built (a scene from a newer release)
        /// does not shift every id after it onto the wrong part.</summary>
        private readonly List<long> _keys;

        public DuplicateGroupCommand(SceneEditor editor, List<PartInstanceData> data)
        {
            _editor = editor;
            _data = data;
            _keys = new List<long>(new long[data.Count]);
        }

        public void Execute()
        {
            var placed = new List<PlacedPart>(_data.Count);
            for (int i = 0; i < _data.Count; i++)
            {
                var made = _editor.SpawnFromData(_data[i], notify: true, reviveKey: _keys[i]);
                if (made is null) continue;

                if (_data[i].Id.Length == 0) _data[i].Id = made.InstanceId;
                if (_keys[i] == 0) _keys[i] = made.Key;
                placed.Add(made);
            }
            // The copies become the selection, so a second Ctrl+D walks the
            // whole group on again rather than repeating from the original.
            _editor.SelectAll(placed, add: false);
        }

        public void Undo()
        {
            _editor.DeselectPart();
            foreach (long key in _keys) _editor.RemovePart(key);
        }
    }

    /// <summary>
    /// Where a duplicate goes, relative to what it was copied from (BF-02).
    ///
    /// One grid cell was wrong twice over. A belt is three cells long, so the
    /// copy landed *inside* its source; and because nothing selected the copy,
    /// the next Ctrl+D duplicated the original again and put a second part in
    /// the same cell, invisibly.
    ///
    /// The offset is the part's own footprint along the direction it faces,
    /// measured from its meshes and rounded up to a whole cell so the result is
    /// still on the grid everything else snaps to. A duplicated belt lands end
    /// to end with its source; a duplicated sensor lands in the next cell.
    /// </summary>
    private Vector3 DuplicateOffset(Node3D node)
    {
        float cell = Grid?.CellSize ?? 0.5f;
        float span = PartBounds.Measure(node).Size.X;

        // Ceiling to a whole cell, and never zero: a part that draws nothing
        // along X would otherwise duplicate onto itself.
        float steps = Mathf.Max(1.0f, Mathf.Ceil(span / cell));
        return node.Basis * new Vector3(cell * steps, 0, 0);
    }

    /// <summary>Select a part the editor already holds — after a duplicate, so
    /// the next Ctrl+D walks on from the copy rather than repeating from the
    /// original. Same three effects a click's selection has, so the gizmo and
    /// the inspector cannot end up describing a different part from the one
    /// the next keystroke will act on.</summary>
    private void SelectPlaced(PlacedPart part) => SelectOnly(part);

    /// <summary>
    /// Shift the selection one grid cell (BF-03).
    ///
    /// <paramref name="screenDelta"/> is in screen terms — (1,0) is "right" as
    /// the user sees it — and is turned into world axes through the camera's
    /// heading, because a nudge that moves a part in world +X while the camera
    /// looks down -X sends it the wrong way and reads as a bug rather than as a
    /// convention.
    ///
    /// One press is one undo step, which is the whole point of a nudge: press
    /// it twice too far and Ctrl+Z twice puts it back.
    /// </summary>
    public void NudgeSelectedPart(Vector2 screenDelta)
    {
        if (Mode != EditorMode.Edit) return;
        if (_selection.Count == 0) return;

        float cell = Grid?.CellSize ?? 0.5f;

        // Snap the camera's heading to the nearest quarter turn, so a nudge
        // always lands on the grid instead of sliding a part off it by a
        // fraction of a cell at every odd viewing angle.
        //
        // Atan2(-X, -Z), not Atan2(X, Z) (HP-39). Heading 0 is defined below as
        // "screen right is world +X", which is the view whose camera forward is
        // −Z — and Atan2(0, −1) is π, not 0. The two differ by exactly π at
        // every angle, which negates both axes, so the arrow keys were inverted
        // in *every* view and not only in the front one. The headless fallback
        // has no camera and leaves the heading at 0, which is why
        // --self-test=buildflow never saw it: the bug lives entirely in the
        // branch a headless test cannot enter. --self-test=nudge supplies a real
        // camera for that reason.
        float heading = 0.0f;
        if (GetViewport()?.GetCamera3D() is { } camera)
        {
            Vector3 forward = -camera.GlobalBasis.Z;
            heading = Mathf.Round(Mathf.Atan2(-forward.X, -forward.Z) / (Mathf.Pi / 2.0f))
                      * (Mathf.Pi / 2.0f);
        }

        // Screen right is world +X at heading 0, and screen "up" is away from
        // the camera, which on the work plane is -Z.
        var world = new Basis(Vector3.Up, heading) * new Vector3(screenDelta.X, 0, -screenDelta.Y);
        Vector3 step = world * cell;
        if (step.IsZeroApprox()) return;

        var moves = new List<IEditorCommand>(_selection.Count);
        foreach (var entry in _selection)
        {
            Vector3 from = entry.Node.Position;
            moves.Add(new MoveCommand(this, entry.Key, from, from + step));
        }

        _history.ExecuteCommand(CompositeCommand.Of(moves));
        MarkDirty();
    }

    /// <summary>
    /// A duplicate, whose redo puts back the same part rather than a new one
    /// (HP-20).
    ///
    /// <see cref="PartCommand"/> already carried its minted id across a redo,
    /// with a comment explaining why: "without it a redo minted a fresh id and
    /// silently broke any driver wiring pointing at the old one." The duplicate
    /// path never got the same treatment — it respawned the same unchanged
    /// <see cref="PartInstanceData"/>, whose <c>Id</c> is deliberately empty, so
    /// every undo/redo cycle handed the part a new name and left a PLC program
    /// addressing a tag prefix that no longer exists.
    /// </summary>
    private sealed class DuplicateCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly PartInstanceData _data;
        private long _key;

        public DuplicateCommand(SceneEditor editor, PartInstanceData data)
        {
            _editor = editor;
            _data = data;
        }

        public void Execute()
        {
            var placed = _editor.SpawnFromData(_data, notify: true, reviveKey: _key);
            if (placed is null) return;

            if (_data.Id.Length == 0) _data.Id = placed.InstanceId;
            if (_key == 0) _key = placed.Key;

            // Selecting here rather than at the call site so a *redo* selects
            // it too: without that, redoing a duplicate leaves the gizmo on
            // whatever was selected before and the next Ctrl+D walks from the
            // wrong part.
            _editor.SelectPlaced(placed);
        }

        public void Undo() => _editor.RemovePart(_key);
    }

    /// <summary>Rotate the selected part 90° in place, undoable. Before this,
    /// R only rotated a part while it was still following the cursor — to
    /// rotate one already placed you had to press M, then R, then re-commit
    /// with a click. See FF-20.</summary>
    public void RotateSelectedPart()
    {
        if (_selection.Count == 0) return;

        // Each part about its own centre, not the group's. Turning a line of
        // belts should turn each belt, which is what somebody who selected five
        // of them and pressed R is asking for; swinging them all about a shared
        // pivot would scatter them off the grid.
        var turns = new List<IEditorCommand>(_selection.Count);
        foreach (var entry in _selection)
        {
            var from = entry.Node.Rotation;
            var to = new Vector3(from.X, from.Y + Mathf.Pi / 2.0f, from.Z);
            turns.Add(new RotateCommand(this, entry.Key, from, to));
        }

        _history.ExecuteCommand(CompositeCommand.Of(turns));
        MarkDirty();
    }

    /// <summary>A turn, resolved the same way <see cref="MoveCommand"/> resolves
    /// a move, and for the same reason.</summary>
    private sealed class RotateCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly long _key;
        private readonly Vector3 _from;
        private readonly Vector3 _to;

        public RotateCommand(SceneEditor editor, long key, Vector3 from, Vector3 to)
        {
            _editor = editor;
            _key = key;
            _from = from;
            _to = to;
        }

        public void Execute() => TurnTo(_to);
        public void Undo() => TurnTo(_from);

        private void TurnTo(Vector3 to)
        {
            if (_editor.FindPlaced(_key) is { } part && GodotObject.IsInstanceValid(part.Node))
                part.Node.Rotation = to;
        }
    }

    /// <summary>Clear pushed onto the history as a single undoable step,
    /// rather than dropping history the way loading a different scene does.
    /// Older history is still dropped: it refers to nodes this just freed, and
    /// undoing Clear rebuilds new instances, not the originals.</summary>
    private sealed class ClearSceneCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly List<PartInstanceData> _snapshot;

        public ClearSceneCommand(SceneEditor editor, List<PartInstanceData> snapshot)
        {
            _editor = editor;
            _snapshot = snapshot;
        }

        public void Execute() => _editor.ClearPlacedPartsCore();
        public void Undo() => _editor.RestorePartsFromSnapshot(_snapshot);
    }

    /// <summary>What the toolbar's Clear button actually calls: same wipe as
    /// <see cref="ClearAllPlacedParts"/>, but a single Ctrl+Z brings it back.
    /// Clear is the most destructive action in the editor and the one most
    /// likely to be a mis-click, so it is the one Clear-family action worth
    /// paying for an undo step on.</summary>
    public void ClearAllPlacedPartsWithUndo()
    {
        if (_placedParts.Count == 0)
        {
            ClearAllPlacedParts();
            return;
        }

        var snapshot = CapturePartsSnapshot();
        _history.ExecuteAsOnly(new ClearSceneCommand(this, snapshot));
        MarkDirty();
        GD.Print("Cleared all editor placed parts (Ctrl+Z to restore)");
    }

    private void UpdatePreviewPosition()
    {
        if (_previewNode is null) return;

        var camera = GetViewport().GetCamera3D();
        if (camera is null) return;

        var mousePos = GetViewport().GetMousePosition();
        if (WorkPlanePoint(camera.ProjectRayOrigin(mousePos),
                           camera.ProjectRayNormal(mousePos)) is { } point)
            _previewNode.Position = point;
    }

    /// <summary>
    /// Where a ray meets the work plane, snapped to the grid and clamped to
    /// the build volume — the one answer to "the cursor is here, so the part
    /// goes there". Placement and drag-to-move (OP-08) both come through here
    /// rather than each doing their own projection, because a part that
    /// snapped differently depending on how it got somewhere is a part whose
    /// saved position depends on how you moved it.
    ///
    /// Null when the ray runs parallel to the plane or points away from it.
    /// </summary>
    private Vector3? WorkPlanePoint(Vector3 from, Vector3 dir)
    {
        if (Mathf.Abs(dir.Y) <= 0.001f) return null;

        float t = (PartLayout.WorkPlaneY - from.Y) / dir.Y;
        if (t <= 0) return null;

        var hitPoint = from + dir * t;
        var snapped = Grid?.SnapToGrid(hitPoint) ?? hitPoint;

        // Clamp to the build volume the grid displays (FF-24) — the floor and
        // its collision extend well past it so a carton that outruns a line
        // still lands somewhere, but nothing should be *placed* out past the
        // visible grid where the student building it can no longer see where
        // its edges are.
        if (Grid is not null)
        {
            float maxX = Grid.GridExtentX * Grid.CellSize;
            float maxZ = Grid.GridExtentZ * Grid.CellSize;
            snapped.X = Mathf.Clamp(snapped.X, -maxX, maxX);
            snapped.Z = Mathf.Clamp(snapped.Z, -maxZ, maxZ);
        }

        return new Vector3(snapped.X, PartLayout.WorkPlaneY, snapped.Z);
    }

    /// <summary>
    /// Drop the held part at a chosen cell.
    ///
    /// The mouse path is "follow the cursor, then place"; this is the same
    /// second half with the first half supplied, so a headless test drives the
    /// real placement rather than a copy of it. There is no camera in a
    /// headless run, and <see cref="UpdatePreviewPosition"/> projects a ray
    /// through one.
    /// </summary>
    public void PlacePreviewAt(Vector3 position)
    {
        if (_previewNode is null) return;
        MovePreviewTo(position);
        PlaceCurrentPart();
    }

    /// <summary>Slide the ghost to a cell without putting it down — what the
    /// cursor does for the several seconds somebody spends deciding. Exposed
    /// for the same reason as <see cref="PlacePreviewAt"/>: a headless run has
    /// no camera for <see cref="UpdatePreviewPosition"/> to project through.
    /// </summary>
    public void MovePreviewTo(Vector3 position)
    {
        if (_previewNode is null) return;
        _previewNode.Position = new Vector3(position.X, PartLayout.WorkPlaneY, position.Z);
    }

    /// <summary>The part type the placement tool is holding, or null. The
    /// palette shows this; a test asserts it.</summary>
    public string? ArmedPartType => _activePartType;

    /// <summary>Put the held part down. What Escape and a right-click both do,
    /// exposed so a headless test can reach the same path rather than a copy
    /// of it.</summary>
    public void CancelPlacement() => ClearPreview();

    /// <summary>Turn the ghost a quarter turn — what R does while placing.</summary>
    public void RotatePreview()
    {
        if (_previewNode is null) return;
        _previewRotationY += Mathf.Pi / 2.0f;
        _previewNode.Rotation = new Vector3(0, _previewRotationY, 0);
    }

    /// <summary>The ghost's heading, so a test can check it survives a
    /// placement.</summary>
    public float PreviewRotationY => _previewRotationY;

    /// <summary>Pick the part up — what M does.</summary>
    public void StartMoveSelected() => StartMoveSelectedPart();

    /// <summary>Select the nth placed part. For headless tests, which have no
    /// camera to click through.</summary>
    public void SelectPartByIndex(int index)
    {
        if (index < 0 || index >= _placedParts.Count) return;
        SelectPlaced(_placedParts[index]);
    }

    /// <summary>Add the nth placed part to the selection, or take it out —
    /// what Shift+click does, without a camera.</summary>
    public void ToggleSelectionByIndex(int index)
    {
        if (index < 0 || index >= _placedParts.Count) return;
        ToggleSelection(_placedParts[index]);
    }

    /// <summary>Where each selected part stands, in selection order.</summary>
    public IReadOnlyList<Vector3> SelectedPositions()
    {
        var at = new List<Vector3>(_selection.Count);
        foreach (var part in _selection) at.Add(part.Node.Position);
        return at;
    }

    /// <summary>Each selected part's heading, in selection order.</summary>
    public IReadOnlyList<float> SelectedHeadings()
    {
        var headings = new List<float>(_selection.Count);
        foreach (var part in _selection) headings.Add(part.Node.Rotation.Y);
        return headings;
    }

    /// <summary>Where the selected part stands, or null when nothing is
    /// selected.</summary>
    public Vector3? SelectedPosition => _selectedPart?.Node.Position;

    /// <summary>The primary selection's heading, for a test that presses R and
    /// has to see whether anything turned.</summary>
    public float? SelectedRotationY => _selectedPart?.Node.Rotation.Y;

    private void PlaceCurrentPart()
    {
        if (_previewNode is null || _activePartType is null) return;

        Vector3 to = _previewNode.Position;
        Vector3 facing = _previewNode.Rotation;

        // Committing a move (HP-04).
        //
        // The part never leaves the scene. It is the same node, the same
        // instance id, the same tags and the same settings, put down somewhere
        // else — exactly what dragging it there would have been, and recorded as
        // the same MoveCommand a drag records.
        //
        // It used to destroy the original and record a *placement* at the
        // destination, so Ctrl+Z after an M-move deleted the part outright
        // rather than putting it back, and what a redo rebuilt was a
        // factory-default part wearing the old id.
        if (_movingPart is { } moving)
        {
            _movingPart = null;
            ClearPreview();

            var steps = new List<IEditorCommand>(2);
            if (!moving.Node.Position.IsEqualApprox(to))
                steps.Add(new MoveCommand(this, moving.Key, moving.Node.Position, to));
            if (!moving.Node.Rotation.IsEqualApprox(facing))
                steps.Add(new RotateCommand(this, moving.Key, moving.Node.Rotation, facing));

            // A move that went nowhere pushes nothing, the same way a drag that
            // never moved the part pushes nothing: Ctrl+Z should undo whatever
            // you did before, not a move that did not happen.
            if (steps.Count == 0) return;

            _history.ExecuteCommand(CompositeCommand.Of(steps));
            MarkDirty();
            GD.Print($"Moved '{moving.InstanceId}' to {to} (Ctrl+Z to put it back)");
            return;
        }

        // Through the history, so Ctrl+Z can take it back. Nothing used to be
        // recorded at all, which left undo/redo as buttons that did nothing.
        string placedType = _activePartType;
        float placedRotation = _previewRotationY;

        _history.ExecuteCommand(new PartCommand(this, new PartInstanceData
        {
            // Empty, not a placeholder: RegisterPartTags only auto-numbers a
            // fresh id when the preferred one is empty. The command fills this
            // in from what the first execute minted, so a redo restores the same
            // identity instead of a new one.
            Id = "",
            Type = placedType,
            Position = new[] { to.X, to.Y, to.Z },
            Rotation = new[] { facing.X, facing.Y, facing.Z },
            Properties = PartProperties.Capture(_previewNode),
        }, isPlacement: true));
        MarkDirty();
        GD.Print($"Placed component '{placedType}' at {to}");
        ClearPreview();

        // Keep the tool (BF-01). Six conveyors in a line used to be six trips
        // back to the palette, which is the tax every user pays on their first
        // scene. Esc or a right-click puts it down.
        //
        // A committed *move* is the exception, and it has to be: a move is one
        // part going to one place, so re-arming there would leave a ghost of
        // what you just moved, ready to drop a second copy on the next click.
        // That case returned above.
        ArmPreview(placedType, placedRotation);
    }

    /// <summary>Below this, a carton has fallen off the world and is not
    /// coming back: a live RigidBody3D with ContinuousCd doing broad- and
    /// narrow-phase work every physics step, forever, since the only despawn
    /// paths were a remover zone and Reset. See FF-12.</summary>
    private const float KillPlaneY = -2.0f;

    /// <summary>Sweeping every tick would be wasted work for something that
    /// only needs to be noticed within about a second.</summary>
    private const float KillPlaneIntervalSeconds = 1.0f;

    /// <summary>Live cartons above this pause the emitter and raise a
    /// warning, rather than an unattended scene accumulating rigid bodies
    /// without bound. Not a hard limit on anything already alive — cartons
    /// already on the belt are left to reach a remover normally.</summary>
    public const int LiveItemCap = 200;

    private float _killPlaneAccumulator;

    /// <summary>As of the last sweep (at most <see cref="KillPlaneIntervalSeconds"/>
    /// stale) — good enough for a status chip, not a physics guarantee.</summary>
    public int LiveItemCount { get; private set; }
    public bool ItemCapHit { get; private set; }

    public override void _PhysicsProcess(double delta)
    {
        if (Tags is null) return;
        float dt = (float)delta;

        _killPlaneAccumulator += dt;
        if (_killPlaneAccumulator >= KillPlaneIntervalSeconds)
        {
            _killPlaneAccumulator = 0f;
            SweepBoxes();
        }

        // Every part drives itself (HP-34). This was four hundred lines of
        // `switch (part.PartType)` with one arm per machine, in the file
        // furthest from every one of them -- so a tag registered in
        // PartTagManager and never dispatched here was a tag that existed,
        // appeared in the inspector, could be forced and did nothing, which is
        // exactly what happened to the weighing conveyor's fault contact.
        foreach (var part in _placedParts)
        {
            if (part.Node is not IPart driven) continue;
            driven.StepPart(new PartTick(Tags, part.TagIds, part.InstanceId, dt, this));
        }
    }

    // ---------- IPartHost: the little a part legitimately asks of the scene

    /// <summary>Is there room under the live-carton cap for one more?</summary>
    bool IPartHost.CanSpawnItem => LiveItemCount < LiveItemCap;

    /// <summary>Tall, short, tall, short. Shared across every emitter in the
    /// scene, so a line fed by two of them still gets a mixed stream rather
    /// than two independent ones that happen to agree.</summary>
    bool IPartHost.NextAlternate()
    {
        bool value = _emitAlternate;
        _emitAlternate = !_emitAlternate;
        return value;
    }

    /// <summary>The deterministic scene transports its boxes in code and
    /// mirrors one named belt, so changing that belt's speed in the inspector
    /// has to reach it. Which belt that is, is the scene's business and not the
    /// belt's.</summary>
    void IPartHost.NoteTransportSpeed(string instanceId, float speed)
    {
        if (Scene is not null && instanceId == SortingTags.ConveyorId) Scene.TransportSpeed = speed;
    }

    /// <summary>
    /// Free any carton that has fallen off the world, and update the live
    /// count the toolbar's status chip reads. Boxes are children of the
    /// scene root, not <see cref="_placedParts"/> — the same place
    /// <see cref="ResetItems"/> already looks for them.
    /// </summary>
    private void SweepBoxes()
    {
        int count = 0;
        int killed = 0;
        foreach (var child in GetParent()?.GetChildren() ?? new Godot.Collections.Array<Node>())
        {
            if (child is not BoxPhysics box) continue;
            if (box.GlobalPosition.Y < KillPlaneY)
            {
                box.QueueFree();
                killed++;
                continue;
            }
            count++;
        }

        if (killed > 0)
            GD.Print($"kill plane: freed {killed} carton(s) that fell off the world");

        LiveItemCount = count;
        bool overCap = count >= LiveItemCap;
        if (overCap && !ItemCapHit)
        {
            ItemCapHit = true;
            GD.PushWarning($"item cap reached ({LiveItemCap} live cartons) — the emitter is "
                          + "paused until the count drops");
        }
        else if (!overCap && ItemCapHit)
        {
            ItemCapHit = false;
        }
    }

    private void ClearPreview()
    {
        bool wasArmed = _activePartType is not null;

        if (_previewNode is not null)
        {
            _previewNode.QueueFree();
            _previewNode = null;
        }
        _activePartType = null;
        _movingPart = null;   // a cancelled move leaves the original untouched

        // Only on a real change: ClearPreview is called at the top of every
        // arm, and a palette that is told "nothing is armed" between two arms
        // flickers its highlight off and on again on every placement.
        if (wasArmed) EmitSignal(SignalName.PlacementArmedChanged, "");
    }

    /// <summary>Build a part node. The catalog owns the factory, beside the
    /// part's own description, because a second switch here could disagree with
    /// it — and a palette button that places nothing is what that
    /// disagreement looks like (HP-34).</summary>
    private static Node3D? CreatePartNode(string partType) => PartCatalog.Create(partType);
}
