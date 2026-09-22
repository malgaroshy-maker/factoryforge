using System.Collections.Generic;
using FactoryForge.Parts;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
