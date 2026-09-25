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
    ///
    /// <paramref name="TagIndex"/> is the number the part's tag names were
    /// built with ("Tank 3 Level"). Kept so a re-declaration after a setting
    /// changes (<see cref="PartSettingsChanged"/>) builds the same names rather
    /// than renaming every tag the part has.
    /// </summary>
    private sealed record PlacedPart(Node3D Node, string InstanceId, string PartType,
                                     bool OwnsTags, long Key, int TagIndex = 0)
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
        public Dictionary<string, string> TagIds => _tagIds ??= BuildTagIdCache(InstanceId, Node, PartType);

        public void InvalidateTagIds() => _tagIds = null;

        private static Dictionary<string, string> BuildTagIdCache(string instanceId, Node3D node,
                                                                  string partType)
        {
            // Asked of the part rather than read from a hand-kept table here
            // (HP-34). That table was a second copy of the registration switch,
            // and a part whose tags changed in one and not the other dispatched
            // against ids nothing owned.
            //
            // Asked of *this* part, not of the catalog's default-configured
            // probe of its type (IP-16). A part's I/O can depend on its
            // settings -- a 4-20 mA analog channel declares a wire-break
            // contact that the default engineering-unit channel does not -- and
            // a cache built from the type's defaults would hand the tick no id
            // for a tag this part really has.
            IReadOnlyList<string> suffixes;
            if (node is IPart part)
            {
                var probe = new PartTagBuilder(null, instanceId, 0);
                part.DeclareTags(probe);
                suffixes = probe.Suffixes;
            }
            else
            {
                suffixes = PartCatalog.TagSuffixes(partType);
            }
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

    /// <summary>
    /// A setting on a placed part has just changed (the property panel calls
    /// this after every settings row). Returns true when that changed the
    /// part's I/O and the new tag set has been announced.
    ///
    /// Most settings are numbers a part reads, and this does nothing for them.
    /// Some change what the part <em>is</em> on the bus: an analog input
    /// switched to raw counts is an <c>int</c> where it was a <c>float</c>, and
    /// at 4-20 mA it gains a wire-break contact (IP-16). A driver is working
    /// from the tag list it was handed in the last <c>describe</c>, so a type
    /// that changed underneath it has to be announced exactly as a placed or
    /// deleted part is -- through <see cref="TagsChanged"/>, which Main answers
    /// with a fresh <c>describe</c> and a new epoch.
    ///
    /// Asks the part what it would declare now and brings the table into line
    /// (<see cref="PartTagManager.RedeclarePartTags"/>): tags that still mean
    /// the same thing keep their value and any force, and only the ones that
    /// changed are replaced. Nothing here knows which parts can do this; the
    /// comparison is the whole of the knowledge.
    /// </summary>
    public bool PartSettingsChanged(Node3D node)
    {
        if (Tags is null) return false;
        int index = _placedParts.FindIndex(p => p.Node == node);
        if (index < 0) return false;

        var entry = _placedParts[index];
        // A view of tags the simulation owns declares nothing of its own, and
        // re-declaring would take them away from their owner.
        if (!entry.OwnsTags) return false;
        if (!PartTagManager.RedeclarePartTags(entry.Node, entry.InstanceId, entry.TagIndex, Tags))
            return false;

        entry.InvalidateTagIds();
        MarkDirty();
        NotifyTagsChanged();
        GD.Print($"'{entry.InstanceId}' re-declared its I/O after a settings change");
        return true;
    }

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
            // On the active level from the first frame, not the floor until
            // the mouse next moves (IP-15).
            _previewNode.Position = new Vector3(0, PartLayout.PlaneY(ActiveLevel), 0);
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

        // Pick it up on the level it stands on, so the ghost does not jump to
        // whatever level was last placed on. PgUp/PgDn while it is held then
        // carry it to another level -- the move commits as one MoveCommand.
        SetActiveLevel(PartLayout.LevelOf(entry.Node.Position.Y));
        SetPlacementPart(entry.PartType);
        if (_previewNode is not null)
        {
            _previewNode.Position = entry.Node.Position;
            _previewNode.Rotation = entry.Node.Rotation;
        }

        _movingPart = entry;
        DeselectPart();
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
                           camera.ProjectRayNormal(mousePos), ActiveLevel) is { } point)
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
    ///
    /// <paramref name="level"/> picks which work plane (IP-15). The ray is met
    /// at that level's own height rather than at the floor's and lifted
    /// afterwards: seen from an angle, the cell under the cursor on a
    /// mezzanine is not the cell under it on the floor, and a part that landed
    /// a metre from where it was pointed at would read as a broken editor.
    /// </summary>
    private Vector3? WorkPlanePoint(Vector3 from, Vector3 dir, int level)
    {
        if (Mathf.Abs(dir.Y) <= 0.001f) return null;

        float planeY = PartLayout.PlaneY(level);
        float t = (planeY - from.Y) / dir.Y;
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

        return new Vector3(snapped.X, planeY, snapped.Z);
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
    /// Y is ignored and pinned to the active level's plane, as the cursor's
    /// would be.
    /// </summary>
    public void MovePreviewTo(Vector3 position)
    {
        if (_previewNode is null) return;
        _previewNode.Position = new Vector3(position.X, PartLayout.PlaneY(ActiveLevel), position.Z);
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
