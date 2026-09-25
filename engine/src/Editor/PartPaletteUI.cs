using System.Collections.Generic;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Part palette UI panel allowing users to select factory components for placement.
///
/// The list is not written here — it comes from <see cref="PartCatalog"/>, so
/// the palette, the placement factory and the save/load self-test cannot drift
/// apart (CP-20, CP-33). What is written here is how it is *presented*: grouped,
/// searchable, and with a tooltip on every button naming what the part does and
/// which tags it will register, because "Light Array" on its own answers none
/// of the questions somebody scanning twenty-two buttons is actually asking.
/// </summary>
public partial class PartPaletteUI : Control
{
    [Signal] public delegate void PartSelectedEventHandler(string partType);

    private LineEdit _search = null!;
    private VBoxContainer _groups = null!;
    private Label _empty = null!;

    /// <summary>A level button was pressed: +1 up, -1 down (IP-15). Wired by
    /// Main to <see cref="SceneEditor.StepLevel"/>, the same call PgUp/PgDn
    /// make, so the two cannot mean different things.</summary>
    [Signal] public delegate void LevelStepRequestedEventHandler(int delta);

    private Label _levelLabel = null!;

    /// <summary>
    /// The level row, directly under the title: which work plane parts land
    /// on, and two buttons to change it. Here rather than on the toolbar
    /// because it is about placing, and the palette is where placing starts --
    /// and because the toolbar is full. PgUp / PgDn do the same thing and the
    /// tooltip says so, since a key nobody is told about is not a feature.
    /// </summary>
    private void BuildLevelRow(VBoxContainer mainBox)
    {
        var row = new HBoxContainer();
        row.AddThemeConstantOverride("separation", 4);
        mainBox.AddChild(row);

        const string tip = "The level new parts land on: 0 is the floor, each level up is "
                           + "0.9 m higher. With parts selected, this lifts or lowers them "
                           + "instead. Keys: PgUp / PgDn.";

        var down = new Button { Text = "▼", TooltipText = tip, FocusMode = FocusModeEnum.None,
                                CustomMinimumSize = new Vector2(32, 28) };
        down.Pressed += () => EmitSignal(SignalName.LevelStepRequested, -1);
        row.AddChild(down);

        _levelLabel = new Label
        {
            TooltipText = tip,
            MouseFilter = MouseFilterEnum.Pass,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        _levelLabel.AddThemeFontSizeOverride("font_size", 12);
        row.AddChild(_levelLabel);

        var up = new Button { Text = "▲", TooltipText = tip, FocusMode = FocusModeEnum.None,
                              CustomMinimumSize = new Vector2(32, 28) };
        up.Pressed += () => EmitSignal(SignalName.LevelStepRequested, +1);
        row.AddChild(up);

        ShowLevel(0);
    }

    /// <summary>Follow the editor's active level. Driven from
    /// <see cref="SceneEditor.ActiveLevelChanged"/>, not from this palette's
    /// own buttons, because PgUp/PgDn and a scene load change it too.</summary>
    public void ShowLevel(int level)
    {
        if (_levelLabel is null) return;
        _levelLabel.Text = level == 0
            ? "LEVEL 0 · floor"
            : $"LEVEL {level} · +{Parts.PartLayout.FloorY(level):0.0} m";
        _levelLabel.AddThemeColorOverride("font_color",
            level == 0 ? new Color(0.75f, 0.78f, 0.84f) : new Color(0.98f, 0.80f, 0.35f));
    }

    /// <summary>Every button, with the text it can be matched against, so
    /// filtering is a visibility pass rather than a rebuild — a rebuild would
    /// drop focus out of the search box on every keystroke.</summary>
    private readonly List<(Button Button, string Haystack)> _entries = new();
    private readonly List<(Control Header, Control Body)> _sections = new();

    /// <summary>Hide the palette while the line is running. Offering parts you
    /// cannot place would be a menu of dead buttons, and it frees the left of
    /// the screen for the machine you are actually operating.</summary>
    public void ShowForMode(bool running) => Visible = !running;

    public override void _Ready()
    {
        // Anchored to stretch with the window's height, not a fixed pixel
        // count: twenty-two buttons at 34px plus headings never fit in the old
        // 350px minimum, and Godot's own default window (1152x648, before
        // FF-32) ran them off the bottom entirely with no way to scroll to
        // the rest. Tracking the viewport means this stays correct at any
        // window size instead of needing a second hand-picked constant.
        AnchorLeft = 0; AnchorTop = 0; AnchorRight = 0; AnchorBottom = 1;
        OffsetLeft = 20; OffsetTop = 20; OffsetRight = 232; OffsetBottom = -20;

        var panel = new PanelContainer();
        panel.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(panel);

        var margin = new MarginContainer();
        margin.AddThemeConstantOverride("margin_top", 10);
        margin.AddThemeConstantOverride("margin_bottom", 10);
        margin.AddThemeConstantOverride("margin_left", 10);
        margin.AddThemeConstantOverride("margin_right", 10);
        panel.AddChild(margin);

        var mainBox = new VBoxContainer();
        margin.AddChild(mainBox);

        var title = new Label
        {
            Text = "PARTS PALETTE",
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        title.AddThemeFontSizeOverride("font_size", 16);
        mainBox.AddChild(title);

        BuildLevelRow(mainBox);

        // Search, because twenty-two parts across six groups is past the point
        // where scanning is faster than typing. Matches the label, the group,
        // the summary and the tag suffixes, so "fault", "analog" and "%" all
        // find something useful.
        _search = new LineEdit
        {
            PlaceholderText = "Search parts or tags…",
            ClearButtonEnabled = true,
        };
        _search.TextChanged += OnSearchChanged;
        mainBox.AddChild(_search);

        var scroll = new ScrollContainer
        {
            SizeFlagsVertical = SizeFlags.ExpandFill,
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
        };
        mainBox.AddChild(scroll);

        _groups = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        _groups.AddThemeConstantOverride("separation", 4);
        scroll.AddChild(_groups);

        BuildFromCatalog();

        _empty = new Label
        {
            Text = "Nothing matches that.",
            Visible = false,
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        _empty.AddThemeColorOverride("font_color", new Color(0.65f, 0.68f, 0.74f));
        _groups.AddChild(_empty);
    }

    /// <summary>Every part button by type, for the armed highlight.</summary>
    private readonly Dictionary<string, Button> _buttonsByType = new();

    /// <summary>The label each button started with. Kept because the armed
    /// button's text gains a marker, and reconstructing the original from the
    /// decorated string is the kind of round trip that goes wrong the first
    /// time a label contains the marker character.</summary>
    private static readonly Dictionary<string, string> Labels = BuildLabels();

    private static Dictionary<string, string> BuildLabels()
    {
        var labels = new Dictionary<string, string>();
        foreach (var info in PartCatalog.All) labels[info.Type] = info.Label;
        return labels;
    }

    private void BuildFromCatalog()
    {
        string? currentGroup = null;
        VBoxContainer? body = null;

        foreach (var info in PartCatalog.All)
        {
            if (info.Group != currentGroup)
            {
                currentGroup = info.Group;
                body = AddGroup(info.Group);
            }
            AddPaletteButton(body!, info);
        }
    }

    private VBoxContainer AddGroup(string title)
    {
        var header = new Button
        {
            Text = $"▾ {title}",
            Alignment = HorizontalAlignment.Left,
            Flat = true,
            FocusMode = FocusModeEnum.None,
        };
        header.AddThemeFontSizeOverride("font_size", 12);
        header.AddThemeColorOverride("font_color", new Color(0.98f, 0.80f, 0.35f));
        _groups.AddChild(header);

        var body = new VBoxContainer();
        _groups.AddChild(body);

        header.Pressed += () =>
        {
            body.Visible = !body.Visible;
            header.Text = (body.Visible ? "▾ " : "▸ ") + title;
        };

        _sections.Add((header, body));
        return body;
    }

    private void AddPaletteButton(VBoxContainer container, PartCatalog.PartInfo info)
    {
        var button = new Button
        {
            Text = info.Label,
            CustomMinimumSize = new Vector2(160, 34),
            Alignment = HorizontalAlignment.Left,
            // The tags are the half of this a PLC person is really after: what
            // will appear in the tag list, and therefore in the export, if I
            // place this.
            TooltipText = $"{info.Summary}\n\nTags:  {info.Tags}",
        };
        button.Pressed += () => EmitSignal(SignalName.PartSelected, info.Type);
        container.AddChild(button);

        _buttonsByType[info.Type] = button;
        _entries.Add((button, $"{info.Label} {info.Type} {info.Group} {info.Summary} {info.Tags}".ToLowerInvariant()));
    }

    /// <summary>
    /// Light the button for the part the placement tool is holding, and put the
    /// others out (BF-04).
    ///
    /// The tool stays armed after a placement, which makes building a line
    /// click-click-click -- and makes the editor modal. A mode with nothing on
    /// screen to say which mode it is in is a worse problem than the one that
    /// bought it, so this is not decoration: it is the other half of BF-01.
    ///
    /// Driven from <see cref="SceneEditor.PlacementArmedChanged"/> rather than
    /// from this palette's own button press, because the tool is put down by
    /// four things the palette never hears about -- Escape, a right-click,
    /// entering Run mode, and committing a move. A highlight only the button
    /// could clear would be left lit after every one of them.
    /// </summary>
    public void SetArmed(string partType)
    {
        foreach (var (type, button) in _buttonsByType)
        {
            if (type == partType)
            {
                button.AddThemeColorOverride("font_color", InkOnArmed);
                button.AddThemeColorOverride("font_hover_color", InkOnArmed);
                button.AddThemeStyleboxOverride("normal", ArmedStyle);
                button.AddThemeStyleboxOverride("hover", ArmedStyle);
                button.Text = Labels[type] + "   \u25cf";
            }
            else
            {
                button.RemoveThemeColorOverride("font_color");
                button.RemoveThemeColorOverride("font_hover_color");
                button.RemoveThemeStyleboxOverride("normal");
                button.RemoveThemeStyleboxOverride("hover");
                button.Text = Labels[type];
            }
        }
    }

    private static readonly Color InkOnArmed = new(0.10f, 0.10f, 0.12f);

    /// <summary>Safety yellow, the colour this project already uses for "this
    /// is the live thing" -- the floor markings, the guard frame, the blade of
    /// a stop. Built once and shared: a stylebox per button per arm would
    /// allocate on every placement.</summary>
    private static readonly StyleBoxFlat ArmedStyle = BuildArmedStyle();

    private static StyleBoxFlat BuildArmedStyle()
    {
        var style = new StyleBoxFlat
        {
            BgColor = new Color(0.95f, 0.75f, 0.10f),
            CornerRadiusTopLeft = 3,
            CornerRadiusTopRight = 3,
            CornerRadiusBottomLeft = 3,
            CornerRadiusBottomRight = 3,
        };
        style.SetContentMarginAll(6);
        return style;
    }

    /// <summary>
    /// Filter by visibility rather than by rebuilding. A rebuild would recreate
    /// the LineEdit's siblings under it and cost focus on every keystroke,
    /// which makes a search box that cannot be typed into.
    /// </summary>
    private void OnSearchChanged(string text)
    {
        string needle = text.Trim().ToLowerInvariant();
        bool filtering = needle.Length > 0;
        int matches = 0;

        foreach (var (button, haystack) in _entries)
        {
            bool match = !filtering || haystack.Contains(needle);
            button.Visible = match;
            if (match) matches++;
        }

        // While filtering, a collapsed group would hide its own matches, so the
        // headers step out of the way entirely and the results read as one
        // list. Restored, expanded, when the box is cleared.
        foreach (var (header, body) in _sections)
        {
            if (filtering)
            {
                header.Visible = false;
                body.Visible = true;
            }
            else
            {
                header.Visible = true;
                body.Visible = true;
                if (header is Button b) b.Text = "▾ " + b.Text.TrimStart('▾', '▸', ' ');
            }
        }

        _empty.Visible = filtering && matches == 0;
    }
}
