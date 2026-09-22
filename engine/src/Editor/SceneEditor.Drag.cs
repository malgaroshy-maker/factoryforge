using System.Collections.Generic;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
