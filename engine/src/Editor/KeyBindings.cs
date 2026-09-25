namespace FactoryForge.Editor;

/// <summary>
/// Every keyboard and mouse binding the app has, in one table (CP-21).
///
/// It was written out once, by hand, in the start screen's footer — which is
/// the only place a user could see it, and only before they had opened
/// anything. Once a scene was open there was no way to ask what the keys were
/// short of quitting back to the start screen, and the footer had already
/// drifted: <c>F</c> framed the selection and appeared nowhere.
///
/// One table, two readers: the start screen's footer and the in-app overlay
/// (<see cref="KeyHelpUI"/>, F12). Adding a binding here puts it in both.
/// </summary>
public static class KeyBindings
{
    public sealed record Binding(string Key, string Action, string Group);

    public static readonly Binding[] All =
    {
        new("F1", "Edit / Run mode", "MODES"),
        new("Space", "Pause / resume", "MODES"),
        new("Ctrl+R", "Reset the run", "MODES"),
        new("Esc", "Put the part down, or disarm the fault tool", "MODES"),

        // The two mouse gestures first, and Drag above M deliberately: the
        // drag is what everyone tries, and a key list offering only the
        // keyboard route implies the obvious one does not work.
        new("Click", "Place — the part stays in hand for the next one", "BUILDING"),
        new("Drag", "Move a part — the mouse route", "BUILDING"),
        new("Shift+Click", "Add / remove from the selection", "BUILDING"),
        new("Ctrl+Drag", "Box-select everything inside", "BUILDING"),
        new("M", "Move selected part — the keyboard route", "BUILDING"),
        new("R", "Rotate — while placing, or a selected part", "BUILDING"),
        new("Arrows", "Nudge selected part one cell", "BUILDING"),
        new("PgUp / PgDn", "Level up / down — where parts land, or lift the selection", "BUILDING"),
        new("Ctrl+D", "Duplicate — again to build a line", "BUILDING"),
        new("Ctrl+A", "Select every part", "BUILDING"),
        new("Ctrl+C / V", "Copy / paste — across scenes too", "BUILDING"),
        new("N", "Show part names — the tag prefixes", "BUILDING"),
        new("Del", "Delete selected part", "BUILDING"),
        new("Ctrl+Z / Y", "Undo / redo", "BUILDING"),

        new("C", "Orbit / fly camera", "CAMERA"),
        new("F", "Frame the selection — or the whole line", "CAMERA"),
        new("1 / 2 / 3 / 4", "Iso · top · front · side view", "CAMERA"),
        new("Wheel", "Zoom", "CAMERA"),
        new("Middle-drag", "Pan", "CAMERA"),

        new("Ctrl+S / O", "Save / open a scene", "PLC & FILES"),
        new("F4", "I/O wiring and export", "PLC & FILES"),
        new("F5", "Connect a PLC driver", "PLC & FILES"),
        new("T", "What this scene asks you to build", "PLC & FILES"),
        new("F12", "This key list", "PLC & FILES"),
    };

    /// <summary>Group names in the order they first appear above.</summary>
    public static string[] Groups()
    {
        var order = new System.Collections.Generic.List<string>();
        foreach (var binding in All)
        {
            if (!order.Contains(binding.Group)) order.Add(binding.Group);
        }
        return order.ToArray();
    }

    public static System.Collections.Generic.IEnumerable<Binding> InGroup(string group)
    {
        foreach (var binding in All)
        {
            if (binding.Group == group) yield return binding;
        }
    }
}
