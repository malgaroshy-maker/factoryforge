using System.Collections.Generic;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
