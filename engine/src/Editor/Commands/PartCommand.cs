namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
