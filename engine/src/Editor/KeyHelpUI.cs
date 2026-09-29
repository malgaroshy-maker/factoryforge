using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// The key list, reachable from inside a scene (CP-21).
///
/// Every binding this app has was visible in exactly one place: the start
/// screen's footer, before you had opened anything. The moment a scene was
/// open there was no way to ask what a key did, which is precisely when
/// somebody wants to know. F12 brings it back, over whatever is on screen,
/// and reads from <see cref="KeyBindings"/> so it cannot drift from the
/// footer that shows the same table.
/// </summary>
public partial class KeyHelpUI : Control
{
    private PanelContainer _panel = null!;

    public override void _Ready()
    {
        // Full-rect and mouse-transparent when hidden, so it never eats a
        // click meant for the machine underneath.
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        MouseFilter = MouseFilterEnum.Ignore;
        Visible = false;

        var centre = new CenterContainer();
        centre.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        centre.MouseFilter = MouseFilterEnum.Ignore;
        AddChild(centre);

        _panel = new PanelContainer();
        centre.AddChild(_panel);

        var margin = new MarginContainer();
        foreach (string side in new[] { "margin_top", "margin_bottom", "margin_left", "margin_right" })
            margin.AddThemeConstantOverride(side, 22);
        _panel.AddChild(margin);

        var body = new VBoxContainer();
        body.AddThemeConstantOverride("separation", 10);
        margin.AddChild(body);

        var title = new Label { Text = "KEYS", HorizontalAlignment = HorizontalAlignment.Center };
        title.AddThemeFontSizeOverride("font_size", 18);
        body.AddChild(title);

        var columns = new HBoxContainer();
        columns.AddThemeConstantOverride("separation", 40);
        body.AddChild(columns);

        foreach (string group in KeyBindings.Groups())
        {
            var column = new VBoxContainer();
            column.AddThemeConstantOverride("separation", 5);
            columns.AddChild(column);

            var heading = new Label { Text = group };
            heading.AddThemeFontSizeOverride("font_size", 12);
            heading.AddThemeColorOverride("font_color", new Color(0.98f, 0.80f, 0.35f));
            column.AddChild(heading);

            foreach (KeyBindings.Binding binding in KeyBindings.InGroup(group))
            {
                var row = new HBoxContainer();
                var key = new Label { Text = binding.Key, CustomMinimumSize = new Vector2(96, 0) };
                key.AddThemeColorOverride("font_color", new Color(0.55f, 0.85f, 1.0f));
                key.AddThemeFontSizeOverride("font_size", 12);
                row.AddChild(key);

                var action = new Label { Text = binding.Action };
                action.AddThemeFontSizeOverride("font_size", 12);
                action.AddThemeColorOverride("font_color", new Color(0.75f, 0.78f, 0.83f));
                row.AddChild(action);

                column.AddChild(row);
            }
        }

        var dismiss = new Label
        {
            Text = "F12 or Esc to close",
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        dismiss.AddThemeFontSizeOverride("font_size", 11);
        dismiss.AddThemeColorOverride("font_color", new Color(0.55f, 0.58f, 0.64f));
        body.AddChild(dismiss);

        var credit = new Label { Text = Credits.Line, HorizontalAlignment = HorizontalAlignment.Center };
        credit.AddThemeFontSizeOverride("font_size", 11);
        credit.AddThemeColorOverride("font_color", new Color(0.55f, 0.58f, 0.64f));
        body.AddChild(credit);
    }

    public void Toggle() => Visible = !Visible;

    /// <summary>Close if open, and say whether it did — so Escape can be spent
    /// on this overlay and not also cancel a placement behind it.</summary>
    public bool DismissIfOpen()
    {
        if (!Visible) return false;
        Visible = false;
        return true;
    }
}
