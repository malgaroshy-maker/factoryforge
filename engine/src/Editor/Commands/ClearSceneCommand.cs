using System.Collections.Generic;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
