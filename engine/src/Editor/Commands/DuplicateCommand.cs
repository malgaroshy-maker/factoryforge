namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
