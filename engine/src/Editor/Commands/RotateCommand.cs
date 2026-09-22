using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
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
}
