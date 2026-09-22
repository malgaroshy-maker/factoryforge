using System.Collections.Generic;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
