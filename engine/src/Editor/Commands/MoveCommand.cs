using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
    /// <summary>
    /// A move, remembered by the part's key rather than by its node (HP-05).
    ///
    /// The node is the wrong handle. Undoing a delete rebuilds the part as a new
    /// <c>Node3D</c>, so a move recorded before that delete was writing a
    /// position into an object Godot had already freed — which throws, and
    /// <see cref="EditorCommandHistory"/> then lost the history entry along with
    /// the exception. Nudge a group, delete it, let a frame pass, Ctrl+Z twice,
    /// and the second Z did nothing with no sign of why.
    /// </summary>
    private sealed class MoveCommand : IEditorCommand
    {
        private readonly SceneEditor _editor;
        private readonly long _key;
        private readonly Vector3 _from;
        private readonly Vector3 _to;

        public MoveCommand(SceneEditor editor, long key, Vector3 from, Vector3 to)
        {
            _editor = editor;
            _key = key;
            _from = from;
            _to = to;
        }

        public void Execute() => MoveTo(_to);
        public void Undo() => MoveTo(_from);

        private void MoveTo(Vector3 at)
        {
            // A part that is genuinely gone is not an error: Clear drops the
            // history that referred to it, but an ordinary delete does not, and
            // undoing past a delete should skip the move rather than throw.
            if (_editor.FindPlaced(_key) is { } part && GodotObject.IsInstanceValid(part.Node))
                part.Node.Position = at;
        }
    }
}
