using System.Collections.Generic;
using Godot;

namespace FactoryForge.Editor;

public interface IEditorCommand
{
    void Execute();
    void Undo();
}

/// <summary>
/// Maintains undo/redo command history stack for Scene Editor actions (Ctrl+Z / Ctrl+Y).
/// </summary>
public class EditorCommandHistory
{
    private readonly Stack<IEditorCommand> _undoStack = new();
    private readonly Stack<IEditorCommand> _redoStack = new();

    public void ExecuteCommand(IEditorCommand command)
    {
        command.Execute();
        _undoStack.Push(command);
        _redoStack.Clear();
    }

    /// <summary>Execute a command and make it the *only* thing left to undo,
    /// dropping whatever history came before it. For a command whose Undo
    /// rebuilds state that invalidates every earlier command's references —
    /// Scene Clear being the case this exists for, since the commands below it
    /// point at nodes the clear just freed.</summary>
    public void ExecuteAsOnly(IEditorCommand command)
    {
        command.Execute();
        _undoStack.Clear();
        _undoStack.Push(command);
        _redoStack.Clear();
    }

    /// <summary>
    /// Undo the last step.
    ///
    /// The entry moves stacks only after its <c>Undo</c> has returned. It used
    /// to be popped first, so a command that threw — which every move and rotate
    /// command could, by writing into a node a delete had already freed — lost
    /// the history entry as well as raising the exception. The step vanished
    /// from both stacks and the scene was left half-undone, with nothing on
    /// screen to say so.
    /// </summary>
    public bool Undo()
    {
        if (_undoStack.Count == 0) return false;
        var cmd = _undoStack.Peek();
        cmd.Undo();
        _undoStack.Pop();
        _redoStack.Push(cmd);
        return true;
    }

    public bool Redo()
    {
        if (_redoStack.Count == 0) return false;
        var cmd = _redoStack.Peek();
        cmd.Execute();
        _redoStack.Pop();
        _undoStack.Push(cmd);
        return true;
    }

    public void Clear()
    {
        _undoStack.Clear();
        _redoStack.Clear();
    }
}
