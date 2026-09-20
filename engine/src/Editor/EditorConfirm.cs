using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// One place for every "this discards something" prompt — Clear, loading a
/// scene over unsaved changes, leaving for the start screen, and quitting.
/// Before this there was no <c>ConfirmationDialog</c> anywhere in the project,
/// which is how Clear could wipe twenty minutes of work on a single mis-click.
/// </summary>
public static class EditorConfirm
{
    public static void Ask(Node parent, string title, string text, System.Action onConfirm)
    {
        var dialog = new ConfirmationDialog
        {
            Title = title,
            DialogText = text,
        };
        dialog.Confirmed += () => { onConfirm(); dialog.QueueFree(); };
        dialog.Canceled += dialog.QueueFree;
        parent.AddChild(dialog);
        dialog.PopupCentered();
    }

    /// <summary>Say something went wrong, where the person is actually looking.
    /// The console is not it: a failed save printed "Saved scene to …" for as
    /// long as this project has existed, and even once it printed the truth,
    /// nobody who just pressed Save is reading stdout.</summary>
    public static void Tell(Node parent, string title, string text)
    {
        var dialog = new AcceptDialog
        {
            Title = title,
            DialogText = text,
        };
        dialog.Confirmed += dialog.QueueFree;
        dialog.Canceled += dialog.QueueFree;
        parent.AddChild(dialog);
        dialog.PopupCentered();
    }
}
