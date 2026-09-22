using System.Collections.Generic;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
    /// <summary>
    /// Several edits that undo and redo as one.
    ///
    /// Every group action here is a list of the single-part commands that
    /// already existed, which is deliberate: deleting five parts is five
    /// deletes, and reusing the command that was already correct for one is
    /// less to get wrong than a second, bulk implementation of the same thing.
    /// Undo runs them backwards, because a group can contain commands whose
    /// effects depend on order.
    /// </summary>
    private sealed class CompositeCommand : IEditorCommand
    {
        private readonly List<IEditorCommand> _steps;

        private CompositeCommand(List<IEditorCommand> steps) => _steps = steps;

        /// <summary>One command for one step, so a single-part edit still
        /// pushes exactly what it used to and nothing has to special-case a
        /// group of one.</summary>
        public static IEditorCommand Of(List<IEditorCommand> steps) =>
            steps.Count == 1 ? steps[0] : new CompositeCommand(steps);

        public void Execute()
        {
            foreach (var step in _steps) step.Execute();
        }

        public void Undo()
        {
            for (int i = _steps.Count - 1; i >= 0; i--) _steps[i].Undo();
        }
    }
}
