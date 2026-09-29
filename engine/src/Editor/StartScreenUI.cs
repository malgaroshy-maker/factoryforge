using System.Collections.Generic;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// What you see when FactoryForge opens.
///
/// Before this, the engine came up straight into the sorting demo with no way
/// to start from anything else and no statement of what the keys do — fine when
/// the only person running it wrote it, hopeless as a first impression.
///
/// It is an overlay on top of the normal startup rather than a separate scene.
/// The engine still builds the sorting line behind it, so choosing "Sorting by
/// height" is just dismissing the panel, and every other choice goes through
/// the same clear/load paths the toolbar already uses and the self-tests
/// already cover.
/// </summary>
public partial class StartScreenUI : Control
{
    /// <summary>Start from a shipped template file (res:// path).</summary>
    [Signal] public delegate void TemplateChosenEventHandler(string path);
    /// <summary>Start from the built-in sorting line.</summary>
    [Signal] public delegate void DefaultSceneChosenEventHandler();
    /// <summary>Start from nothing.</summary>
    [Signal] public delegate void EmptySceneChosenEventHandler();
    /// <summary>Open a scene the user saved.</summary>
    [Signal] public delegate void OpenRequestedEventHandler(string path);
    /// <summary>Start the reference line and run it with the built-in demo
    /// driver — no PLC, no Python, nothing to install. See FF-23.</summary>
    [Signal] public delegate void DemoRequestedEventHandler();

    /// <summary>
    /// Each one teaches a different thing, which is the point of having more
    /// than one. The blurb says what you would learn, not what parts are in it.
    /// Loaded from <c>engine/templates/manifest.json</c> via <see cref="TemplateManifest"/>
    /// rather than hardcoded here, so this list and the template self-test's
    /// cannot silently drift apart (UX-13).
    /// </summary>
    private static readonly IReadOnlyList<TemplateEntry> Templates = TemplateManifest.Load();

    private const string RecentPath = "user://recent_scenes.json";
    private VBoxContainer _recentBox = null!;

    /// <summary>
    /// Every clickable row on this screen used to render as flat, unstyled
    /// text — no border, no background, no hover — on the one screen a new
    /// user meets before learning any of the keys (FF-22). A panel background
    /// that brightens on hover and a focus ring make "this is a button" and
    /// "this is what you are about to activate" both obvious, including from
    /// the keyboard.
    /// </summary>
    private static void StyleClickable(Button button)
    {
        button.AddThemeStyleboxOverride("normal", CardStyle(new Color(1, 1, 1, 0.04f)));
        button.AddThemeStyleboxOverride("hover", CardStyle(new Color(1, 1, 1, 0.10f), new Color(0.98f, 0.80f, 0.35f, 0.6f)));
        button.AddThemeStyleboxOverride("pressed", CardStyle(new Color(1, 1, 1, 0.16f)));
        button.AddThemeStyleboxOverride("focus", CardStyle(new Color(1, 1, 1, 0.06f), new Color(0.55f, 0.85f, 1.0f, 0.9f)));
    }

    private static StyleBoxFlat CardStyle(Color background, Color? border = null)
    {
        var box = new StyleBoxFlat
        {
            BgColor = background,
            CornerRadiusTopLeft = 6,
            CornerRadiusTopRight = 6,
            CornerRadiusBottomLeft = 6,
            CornerRadiusBottomRight = 6,
            ContentMarginLeft = 10,
            ContentMarginRight = 10,
            ContentMarginTop = 6,
            ContentMarginBottom = 6,
        };
        if (border is { } b)
        {
            box.BorderColor = b;
            box.BorderWidthLeft = box.BorderWidthRight = box.BorderWidthTop = box.BorderWidthBottom = 1;
        }
        return box;
    }

    public override void _Ready()
    {
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);

        var backdrop = new ColorRect { Color = new Color(0.05f, 0.06f, 0.08f, 0.93f) };
        backdrop.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(backdrop);

        // A ScrollContainer, not a bare CenterContainer: the 980x640 card
        // used to be taller than Godot's own default 1152x648 window, with no
        // way to reach the Quit button or the key list it clipped off the
        // bottom. project.godot now opens wider (FF-32), but a window
        // resized smaller still degrades to scrolling instead of clipping.
        var scroll = new ScrollContainer
        {
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
        };
        scroll.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(scroll);

        var centre = new CenterContainer
        {
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            SizeFlagsVertical = SizeFlags.ExpandFill,
        };
        scroll.AddChild(centre);

        var card = new PanelContainer { CustomMinimumSize = new Vector2(980, 640) };
        centre.AddChild(card);

        var margin = new MarginContainer();
        foreach (string side in new[] { "margin_top", "margin_bottom", "margin_left", "margin_right" })
            margin.AddThemeConstantOverride(side, 28);
        card.AddChild(margin);

        var page = new VBoxContainer();
        margin.AddChild(page);

        var title = new Label { Text = "FactoryForge" };
        title.AddThemeFontSizeOverride("font_size", 34);
        page.AddChild(title);

        var subtitle = new Label
        {
            Text = "A 3D factory to write PLC programs against. Pick something to start from.",
        };
        subtitle.AddThemeColorOverride("font_color", new Color(0.72f, 0.75f, 0.80f));
        page.AddChild(subtitle);

        // The single highest-value button on this screen: launched cold,
        // nothing moves until a PLC or a sidecar is connected, which reads as
        // "broken" in the first thirty seconds. This runs the reference line
        // with an in-engine stand-in driver — nothing to install. See FF-23.
        var demoBtn = new Button
        {
            Text = "  ▶  Watch it run — see the factory move before you touch anything",
            Alignment = HorizontalAlignment.Left,
            CustomMinimumSize = new Vector2(0, 46),
            TooltipText = "Starts the reference sorting line, driven by a built-in demo — "
                         + "no PLC or Python required. Stands down the moment you connect a real driver.",
        };
        demoBtn.AddThemeFontSizeOverride("font_size", 15);
        demoBtn.AddThemeStyleboxOverride("normal", CardStyle(new Color(0.25f, 0.55f, 0.35f, 0.55f), new Color(0.45f, 0.95f, 0.55f, 0.7f)));
        demoBtn.AddThemeStyleboxOverride("hover", CardStyle(new Color(0.30f, 0.65f, 0.42f, 0.65f), new Color(0.45f, 0.95f, 0.55f, 0.9f)));
        demoBtn.AddThemeStyleboxOverride("pressed", CardStyle(new Color(0.22f, 0.48f, 0.30f, 0.7f), new Color(0.45f, 0.95f, 0.55f, 0.9f)));
        demoBtn.AddThemeStyleboxOverride("focus", CardStyle(new Color(0.25f, 0.55f, 0.35f, 0.55f), new Color(0.55f, 0.85f, 1.0f, 0.9f)));
        demoBtn.Pressed += () => { EmitSignal(SignalName.DemoRequested); Dismiss(); };
        page.AddChild(demoBtn);

        page.AddChild(new HSeparator());

        var columns = new HBoxContainer { SizeFlagsVertical = SizeFlags.ExpandFill };
        columns.AddThemeConstantOverride("separation", 22);
        page.AddChild(columns);

        BuildTemplateColumn(columns);
        columns.AddChild(new VSeparator());
        BuildSideColumn(columns);

        page.AddChild(new HSeparator());
        BuildControlsFooter(page);

        var credit = new Label { Text = Credits.Line, HorizontalAlignment = HorizontalAlignment.Center };
        credit.AddThemeFontSizeOverride("font_size", 11);
        credit.AddThemeColorOverride("font_color", new Color(0.55f, 0.58f, 0.64f));
        page.AddChild(credit);
    }

    private void BuildTemplateColumn(HBoxContainer columns)
    {
        var left = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        columns.AddChild(left);

        var heading = new Label { Text = "START FROM A TEMPLATE" };
        heading.AddThemeFontSizeOverride("font_size", 13);
        heading.AddThemeColorOverride("font_color", new Color(0.98f, 0.80f, 0.35f));
        left.AddChild(heading);

        var scroll = new ScrollContainer
        {
            SizeFlagsVertical = SizeFlags.ExpandFill,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            // Wrap the blurbs instead of letting them run off the right edge
            // behind a horizontal scrollbar nobody will think to drag.
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
        };
        left.AddChild(scroll);
        var list = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        list.AddThemeConstantOverride("separation", 6);
        scroll.AddChild(list);

        foreach (var template in Templates)
        {
            var button = new Button
            {
                // Height only: a minimum *width* here would force the button
                // wider than the column it lives in, which is what clipped the
                // text in the first place.
                CustomMinimumSize = new Vector2(0, 70),
                SizeFlagsHorizontal = SizeFlags.ExpandFill,
                Alignment = HorizontalAlignment.Left,
                Text = $"  {template.Title}\n  {template.Blurb}",
                AutowrapMode = TextServer.AutowrapMode.WordSmart,
                // The task, not just the blurb: the blurb says what the scene
                // *is* and the brief says what to do with it, and choosing
                // between eight templates is exactly when somebody wants the
                // second one (BR-02).
                TooltipText = TooltipFor(template),
            };
            button.AddThemeFontSizeOverride("font_size", 13);
            StyleClickable(button);

            string path = template.Path;
            button.Pressed += () =>
            {
                if (path.Length == 0) EmitSignal(SignalName.DefaultSceneChosen);
                else EmitSignal(SignalName.TemplateChosen, path);
                Dismiss();
            };
            list.AddChild(button);
        }
    }

    /// <summary>What a template's button says on hover: what the scene is,
    /// then what it is asking you to build and how you know you are done.
    /// Choosing between eight of them is exactly when somebody wants the
    /// second half (BR-02).</summary>
    private static string TooltipFor(TemplateEntry template)
    {
        if (template.Brief is not { } brief) return template.Blurb;
        return template.Blurb
               + "\n\nYOUR TASK\n" + brief.Task
               + "\n\nDONE WHEN\n" + brief.Done;
    }

    private void BuildSideColumn(HBoxContainer columns)
    {
        var right = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        columns.AddChild(right);

        var heading = new Label { Text = "OR" };
        heading.AddThemeFontSizeOverride("font_size", 13);
        heading.AddThemeColorOverride("font_color", new Color(0.98f, 0.80f, 0.35f));
        right.AddChild(heading);

        var blank = new Button
        {
            Text = "  Empty scene",
            Alignment = HorizontalAlignment.Left,
            CustomMinimumSize = new Vector2(300, 38),
            TooltipText = "No parts and no tags — build a line from the palette",
        };
        StyleClickable(blank);
        blank.Pressed += () => { EmitSignal(SignalName.EmptySceneChosen); Dismiss(); };
        right.AddChild(blank);

        var open = new Button
        {
            Text = "  Open a saved scene…",
            Alignment = HorizontalAlignment.Left,
            CustomMinimumSize = new Vector2(300, 38),
        };
        StyleClickable(open);
        open.Pressed += ShowOpenDialog;
        right.AddChild(open);

        right.AddChild(new HSeparator());

        var recentHeading = new Label { Text = "RECENT" };
        recentHeading.AddThemeFontSizeOverride("font_size", 13);
        recentHeading.AddThemeColorOverride("font_color", new Color(0.98f, 0.80f, 0.35f));
        right.AddChild(recentHeading);

        _recentBox = new VBoxContainer { SizeFlagsVertical = SizeFlags.ExpandFill };
        right.AddChild(_recentBox);
        RebuildRecent();

        var quit = new Button { Text = "  Quit", Alignment = HorizontalAlignment.Left };
        StyleClickable(quit);
        quit.Pressed += () => GetTree().Quit();
        right.AddChild(quit);
    }

    private void BuildControlsFooter(VBoxContainer page)
    {
        // The keys used to live only in a markdown file, which is no use to
        // somebody who has just opened the program.
        var grid = new GridContainer { Columns = 4 };
        // Without this the next column's key sits flush against the previous
        // action, and "Edit / Run mode" reads as though Space were part of it.
        grid.AddThemeConstantOverride("h_separation", 26);
        grid.AddThemeConstantOverride("v_separation", 6);
        page.AddChild(grid);

        // Read from the one binding table rather than a hand-written copy
        // (CP-21). The copy that used to live here had already drifted: F
        // framed the selection and was listed nowhere, and the in-app overlay
        // could not have agreed with it even in principle.
        // Typed rather than `var`: the record is the shared shape both readers
        // of this table agree on, and naming it here is the only place in the
        // codebase that says so out loud. A6 checks for exactly that -- a type
        // reached only through inference reads as dead code to anything
        // scanning the source.
        KeyBindings.Binding[] keys = KeyBindings.All;

        foreach (var binding in keys)
        {
            var row = new HBoxContainer { CustomMinimumSize = new Vector2(230, 0) };
            var keyLabel = new Label { Text = binding.Key, CustomMinimumSize = new Vector2(86, 0) };
            keyLabel.AddThemeColorOverride("font_color", new Color(0.55f, 0.85f, 1.0f));
            keyLabel.AddThemeFontSizeOverride("font_size", 12);
            row.AddChild(keyLabel);

            var actionLabel = new Label { Text = binding.Action };
            actionLabel.AddThemeFontSizeOverride("font_size", 12);
            actionLabel.AddThemeColorOverride("font_color", new Color(0.72f, 0.75f, 0.80f));
            row.AddChild(actionLabel);

            grid.AddChild(row);
        }
    }

    private void ShowOpenDialog()
    {
        var dialog = new FileDialog
        {
            FileMode = FileDialog.FileModeEnum.OpenFile,
            Access = FileDialog.AccessEnum.Filesystem,
            Filters = new[] { "*.json ; FactoryForge scene" },
            Title = "Open scene",
            Size = new Vector2I(760, 520),
        };
        dialog.FileSelected += (path) =>
        {
            EmitSignal(SignalName.OpenRequested, path);
            dialog.QueueFree();
            Dismiss();
        };
        dialog.Canceled += dialog.QueueFree;
        AddChild(dialog);
        dialog.PopupCentered();
    }

    private void Dismiss() => Visible = false;

    /// <summary>Show it again — the toolbar's way back to this screen.</summary>
    public void Reopen()
    {
        RebuildRecent();
        Visible = true;
    }

    // --- recent files ---

    private void RebuildRecent()
    {
        foreach (var child in _recentBox.GetChildren()) child.QueueFree();

        var recent = LoadRecent();
        if (recent.Count == 0)
        {
            var none = new Label { Text = "  nothing yet" };
            none.AddThemeFontSizeOverride("font_size", 12);
            none.AddThemeColorOverride("font_color", new Color(0.5f, 0.52f, 0.56f));
            _recentBox.AddChild(none);
            return;
        }

        foreach (string path in recent)
        {
            var button = new Button
            {
                Text = "  " + path.GetFile(),
                TooltipText = path,
                Alignment = HorizontalAlignment.Left,
                CustomMinimumSize = new Vector2(300, 32),
            };
            button.AddThemeFontSizeOverride("font_size", 12);
            StyleClickable(button);
            string captured = path;
            button.Pressed += () =>
            {
                EmitSignal(SignalName.OpenRequested, captured);
                Dismiss();
            };
            _recentBox.AddChild(button);
        }
    }

    public static List<string> LoadRecent()
    {
        var result = new List<string>();
        if (!Godot.FileAccess.FileExists(RecentPath)) return result;

        using var file = Godot.FileAccess.Open(RecentPath, Godot.FileAccess.ModeFlags.Read);
        if (Json.ParseString(file?.GetAsText() ?? "").Obj is not Godot.Collections.Array list)
            return result;

        foreach (var entry in list)
        {
            string path = entry.AsString();
            // A file that has been moved or deleted should not sit in the list
            // offering to open something that is not there.
            if (path.Length > 0 && Godot.FileAccess.FileExists(path)) result.Add(path);
        }
        return result;
    }

    /// <summary>Record a scene as recently used, most recent first.</summary>
    public static void Remember(string path)
    {
        if (path.StartsWith("res://")) return;   // templates are not "recent files"

        var recent = LoadRecent();
        recent.Remove(path);
        recent.Insert(0, path);
        while (recent.Count > 6) recent.RemoveAt(recent.Count - 1);

        var array = new Godot.Collections.Array();
        foreach (string entry in recent) array.Add(entry);

        using var file = Godot.FileAccess.Open(RecentPath, Godot.FileAccess.ModeFlags.Write);
        file?.StoreString(Json.Stringify(array));
    }
}
