using System.Collections.Generic;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
