using System.Collections.Generic;
using System.Linq;
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
///
/// Since V12-06/07 it is a full-screen menu rather than a translucent card over
/// the editor: the logo and the actions down the left over a generated
/// background (engine/assets/ASSETS.md), and every template as a card with a
/// thumbnail rendered from the real engine (tools/make_thumbnails.py), so a
/// picture shows what you will get before you open it.
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
    private const string BackgroundPath = "res://assets/menu/start_background.jpg";
    private const string LogoPath = "res://assets/branding/logo_1024.png";
    private const string ThumbnailDir = "res://templates/thumbnails/";

    // The logo's own colours (tools/make_branding.py), so the menu and the
    // icon are visibly the same product.
    private static readonly Color Navy = new(0.016f, 0.098f, 0.282f);
    private static readonly Color Orange = new(1.0f, 0.54f, 0.08f);
    private static readonly Color Teal = new(0.09f, 0.75f, 0.75f);
    private static readonly Color Muted = new(0.70f, 0.75f, 0.84f);
    private static readonly Color Faint = new(0.50f, 0.56f, 0.66f);

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
        button.AddThemeStyleboxOverride("normal", CardStyle(new Color(Navy, 0.72f), new Color(1, 1, 1, 0.08f)));
        button.AddThemeStyleboxOverride("hover", CardStyle(new Color(0.07f, 0.17f, 0.38f, 0.88f), new Color(Orange, 0.75f)));
        button.AddThemeStyleboxOverride("pressed", CardStyle(new Color(0.05f, 0.13f, 0.30f, 0.95f), new Color(Orange, 0.9f)));
        button.AddThemeStyleboxOverride("focus", CardStyle(new Color(0, 0, 0, 0), new Color(Teal, 0.95f), 2));
    }

    private static StyleBoxFlat CardStyle(Color background, Color? border = null, int borderWidth = 1, int radius = 8)
    {
        var box = new StyleBoxFlat
        {
            BgColor = background,
            CornerRadiusTopLeft = radius,
            CornerRadiusTopRight = radius,
            CornerRadiusBottomLeft = radius,
            CornerRadiusBottomRight = radius,
            ContentMarginLeft = 14,
            ContentMarginRight = 14,
            ContentMarginTop = 8,
            ContentMarginBottom = 8,
        };
        if (border is { } b)
        {
            box.BorderColor = b;
            box.BorderWidthLeft = box.BorderWidthRight = box.BorderWidthTop = box.BorderWidthBottom = borderWidth;
        }
        return box;
    }

    private static Label Heading(string text)
    {
        var label = new Label { Text = text };
        label.AddThemeFontSizeOverride("font_size", 13);
        label.AddThemeColorOverride("font_color", Orange);
        return label;
    }

    /// <summary>A texture if it was shipped, null if not: a checkout that has
    /// not run the asset scripts still gets a working menu, just a plainer one.</summary>
    private static Texture2D? TryLoad(string path) =>
        ResourceLoader.Exists(path) ? GD.Load<Texture2D>(path) : null;

    public override void _Ready()
    {
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);

        // Opaque now: the editor used to show through a 93% tint, which made
        // the first thing anyone saw a blur of panels they had not met yet.
        var backdrop = new ColorRect { Color = Navy };
        backdrop.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(backdrop);

        if (TryLoad(BackgroundPath) is { } background)
        {
            var image = new TextureRect
            {
                Texture = background,
                ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
                StretchMode = TextureRect.StretchModeEnum.KeepAspectCovered,
                MouseFilter = MouseFilterEnum.Ignore,
            };
            image.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
            AddChild(image);
        }

        // Dark on the left where the text is, lighter to the right so the
        // picture still reads behind the template cards.
        var shade = new TextureRect
        {
            Texture = new GradientTexture2D
            {
                Gradient = new Gradient
                {
                    Offsets = new[] { 0f, 0.30f, 0.62f, 1f },
                    Colors = new[] { new Color(Navy, 0.96f), new Color(Navy, 0.90f), new Color(Navy, 0.62f), new Color(Navy, 0.45f) },
                },
                FillFrom = new Vector2(0, 0.5f),
                FillTo = new Vector2(1, 0.5f),
                Width = 256,
                Height = 4,
            },
            ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
            StretchMode = TextureRect.StretchModeEnum.Scale,
            MouseFilter = MouseFilterEnum.Ignore,
        };
        shade.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(shade);

        var margin = new MarginContainer();
        margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        margin.AddThemeConstantOverride("margin_left", 56);
        margin.AddThemeConstantOverride("margin_right", 40);
        margin.AddThemeConstantOverride("margin_top", 44);
        margin.AddThemeConstantOverride("margin_bottom", 28);
        AddChild(margin);

        var columns = new HBoxContainer();
        columns.AddThemeConstantOverride("separation", 48);
        margin.AddChild(columns);

        BuildSideColumn(columns);
        BuildTemplateColumn(columns);
    }

    private void BuildSideColumn(HBoxContainer columns)
    {
        var left = new VBoxContainer { CustomMinimumSize = new Vector2(380, 0) };
        left.AddThemeConstantOverride("separation", 10);
        columns.AddChild(left);

        var header = new HBoxContainer();
        header.AddThemeConstantOverride("separation", 16);
        left.AddChild(header);
        if (TryLoad(LogoPath) is { } logo)
        {
            header.AddChild(new TextureRect
            {
                Texture = logo,
                CustomMinimumSize = new Vector2(76, 76),
                ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
                StretchMode = TextureRect.StretchModeEnum.KeepAspectCentered,
            });
        }
        var name = new HBoxContainer { SizeFlagsVertical = SizeFlags.ShrinkCenter };
        name.AddThemeConstantOverride("separation", 0);
        foreach (var (word, colour) in new[] { ("Factory", Orange), ("Forge", Teal) })
        {
            var part = new Label { Text = word };
            part.AddThemeFontSizeOverride("font_size", 40);
            part.AddThemeColorOverride("font_color", colour);
            name.AddChild(part);
        }
        header.AddChild(name);

        var subtitle = new Label
        {
            Text = "A 3D factory to write PLC programs against.\nPick something to start from.",
        };
        subtitle.AddThemeFontSizeOverride("font_size", 15);
        subtitle.AddThemeColorOverride("font_color", Muted);
        left.AddChild(subtitle);

        left.AddChild(new Control { CustomMinimumSize = new Vector2(0, 14) });

        // The single highest-value button on this screen: launched cold,
        // nothing moves until a PLC or a sidecar is connected, which reads as
        // "broken" in the first thirty seconds. This runs the reference line
        // with an in-engine stand-in driver — nothing to install. See FF-23.
        var demoBtn = new Button
        {
            CustomMinimumSize = new Vector2(0, 66),
            TooltipText = "Starts the reference sorting line, driven by a built-in demo — "
                         + "no PLC or Python required. Stands down the moment you connect a real driver.",
        };
        // Two labels rather than one two-line caption, so the second line can
        // be smaller and both start at the same edge.
        var demoText = new VBoxContainer { MouseFilter = MouseFilterEnum.Ignore, Alignment = BoxContainer.AlignmentMode.Center };
        demoText.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        demoText.OffsetLeft = 18;
        demoText.AddThemeConstantOverride("separation", 0);
        foreach (var (text, size, alpha) in new[] { ("▶  Watch it run", 18, 1f), ("See the factory move — no PLC needed", 13, 0.88f) })
        {
            var line = new Label { Text = text, MouseFilter = MouseFilterEnum.Ignore };
            line.AddThemeFontSizeOverride("font_size", size);
            line.AddThemeColorOverride("font_color", new Color(1, 1, 1, alpha));
            demoText.AddChild(line);
        }
        demoBtn.AddChild(demoText);
        demoBtn.AddThemeStyleboxOverride("normal", CardStyle(new Color(0.88f, 0.44f, 0.04f), new Color(Orange, 1f)));
        demoBtn.AddThemeStyleboxOverride("hover", CardStyle(Orange, new Color(1f, 0.75f, 0.45f)));
        demoBtn.AddThemeStyleboxOverride("pressed", CardStyle(new Color(0.75f, 0.37f, 0.03f), new Color(1f, 0.75f, 0.45f)));
        demoBtn.AddThemeStyleboxOverride("focus", CardStyle(new Color(0, 0, 0, 0), new Color(Teal, 0.95f), 2));
        demoBtn.Pressed += () => { EmitSignal(SignalName.DemoRequested); Dismiss(); };
        left.AddChild(demoBtn);

        var blank = new Button
        {
            Text = "Empty scene",
            Alignment = HorizontalAlignment.Left,
            CustomMinimumSize = new Vector2(0, 42),
            TooltipText = "No parts and no tags — build a line from the palette",
        };
        blank.AddThemeFontSizeOverride("font_size", 15);
        StyleClickable(blank);
        blank.Pressed += () => { EmitSignal(SignalName.EmptySceneChosen); Dismiss(); };
        left.AddChild(blank);

        var open = new Button
        {
            Text = "Open a saved scene…",
            Alignment = HorizontalAlignment.Left,
            CustomMinimumSize = new Vector2(0, 42),
        };
        open.AddThemeFontSizeOverride("font_size", 15);
        StyleClickable(open);
        open.Pressed += ShowOpenDialog;
        left.AddChild(open);

        left.AddChild(new Control { CustomMinimumSize = new Vector2(0, 10) });
        left.AddChild(Heading("RECENT"));
        _recentBox = new VBoxContainer();
        _recentBox.AddThemeConstantOverride("separation", 6);
        left.AddChild(_recentBox);
        RebuildRecent();

        left.AddChild(new Control { SizeFlagsVertical = SizeFlags.ExpandFill });

        BuildEssentialKeys(left);

        var quit = new Button { Text = "Quit", Alignment = HorizontalAlignment.Left, CustomMinimumSize = new Vector2(0, 38) };
        StyleClickable(quit);
        quit.Pressed += () => GetTree().Quit();
        left.AddChild(quit);

        var credit = new Label { Text = Credits.Line };
        credit.AddThemeFontSizeOverride("font_size", 11);
        credit.AddThemeColorOverride("font_color", Faint);
        left.AddChild(credit);
    }

    /// <summary>
    /// The handful of keys somebody needs in their first minute, and where the
    /// rest are. The full grid used to fill the bottom third of this screen;
    /// it lives behind F12 now, which is itself one of the keys listed here.
    /// </summary>
    private static void BuildEssentialKeys(VBoxContainer column)
    {
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
        string[] essential = { "F1", "Space", "F5", "T", "F12" };

        var grid = new GridContainer { Columns = 2 };
        grid.AddThemeConstantOverride("h_separation", 14);
        grid.AddThemeConstantOverride("v_separation", 2);
        foreach (var binding in keys.Where(b => essential.Contains(b.Key)))
        {
            var key = new Label { Text = binding.Key, CustomMinimumSize = new Vector2(52, 0) };
            key.AddThemeFontSizeOverride("font_size", 12);
            key.AddThemeColorOverride("font_color", Teal);
            grid.AddChild(key);

            var action = new Label { Text = binding.Action };
            action.AddThemeFontSizeOverride("font_size", 12);
            action.AddThemeColorOverride("font_color", Faint);
            grid.AddChild(action);
        }
        column.AddChild(grid);
    }

    private void BuildTemplateColumn(HBoxContainer columns)
    {
        var right = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        right.AddThemeConstantOverride("separation", 10);
        columns.AddChild(right);

        var headingRow = new HBoxContainer();
        headingRow.AddChild(Heading("START FROM A TEMPLATE"));
        var count = new Label
        {
            Text = $"{Templates.Count} scenes, each with a task — hover one for it",
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            HorizontalAlignment = HorizontalAlignment.Right,
        };
        count.AddThemeFontSizeOverride("font_size", 12);
        count.AddThemeColorOverride("font_color", Muted);
        headingRow.AddChild(count);
        right.AddChild(headingRow);

        var scroll = new ScrollContainer
        {
            SizeFlagsVertical = SizeFlags.ExpandFill,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            // Wrap the blurbs instead of letting them run off the right edge
            // behind a horizontal scrollbar nobody will think to drag.
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
        };
        right.AddChild(scroll);

        var grid = new GridContainer { Columns = 3, SizeFlagsHorizontal = SizeFlags.ExpandFill };
        grid.AddThemeConstantOverride("h_separation", 14);
        grid.AddThemeConstantOverride("v_separation", 14);
        scroll.AddChild(grid);

        foreach (var template in Templates) grid.AddChild(TemplateCard(template));
    }

    /// <summary>One template: its engine-rendered thumbnail, its title and
    /// its blurb, the whole card one button. The children ignore the mouse so
    /// the click, the hover and the tooltip all land on the button.</summary>
    private Button TemplateCard(TemplateEntry template)
    {
        var card = new Button
        {
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            CustomMinimumSize = new Vector2(0, 268),
            // The task, not just the blurb: the blurb says what the scene
            // *is* and the brief says what to do with it, and choosing
            // between many templates is exactly when somebody wants the
            // second one (BR-02).
            TooltipText = TooltipFor(template),
            ClipContents = true,
        };
        StyleClickable(card);
        card.AddThemeStyleboxOverride("normal", CardStyle(new Color(Navy, 0.82f), new Color(1, 1, 1, 0.08f)));

        var body = new VBoxContainer { MouseFilter = MouseFilterEnum.Ignore };
        body.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        body.OffsetLeft = body.OffsetTop = 8;
        body.OffsetRight = body.OffsetBottom = -8;
        body.AddThemeConstantOverride("separation", 6);
        card.AddChild(body);

        var thumbnail = TryLoad(ThumbnailDir + template.Id + ".jpg");
        Control picture = thumbnail is null
            ? new ColorRect { Color = new Color(1, 1, 1, 0.05f) }
            : new TextureRect
            {
                Texture = thumbnail,
                ExpandMode = TextureRect.ExpandModeEnum.IgnoreSize,
                StretchMode = TextureRect.StretchModeEnum.KeepAspectCovered,
            };
        picture.CustomMinimumSize = new Vector2(0, 168);
        picture.MouseFilter = MouseFilterEnum.Ignore;
        picture.ClipContents = true;
        body.AddChild(picture);

        var title = new Label { Text = template.Title, MouseFilter = MouseFilterEnum.Ignore };
        title.AddThemeFontSizeOverride("font_size", 15);
        title.AddThemeColorOverride("font_color", new Color(0.96f, 0.97f, 1f));
        body.AddChild(title);

        var blurb = new Label
        {
            Text = template.Blurb,
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            MaxLinesVisible = 3,
            TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
            MouseFilter = MouseFilterEnum.Ignore,
            SizeFlagsVertical = SizeFlags.ExpandFill,
        };
        blurb.AddThemeFontSizeOverride("font_size", 12);
        blurb.AddThemeColorOverride("font_color", Muted);
        body.AddChild(blurb);

        string path = template.Path;
        card.Pressed += () =>
        {
            if (path.Length == 0) EmitSignal(SignalName.DefaultSceneChosen);
            else EmitSignal(SignalName.TemplateChosen, path);
            Dismiss();
        };
        return card;
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
            var none = new Label { Text = "Nothing yet — scenes you save appear here." };
            none.AddThemeFontSizeOverride("font_size", 12);
            none.AddThemeColorOverride("font_color", Faint);
            _recentBox.AddChild(none);
            return;
        }

        foreach (string path in recent)
        {
            var button = new Button
            {
                Text = path.GetFile(),
                TooltipText = path,
                Alignment = HorizontalAlignment.Left,
                CustomMinimumSize = new Vector2(0, 34),
                TextOverrunBehavior = TextServer.OverrunBehavior.TrimEllipsis,
            };
            button.AddThemeFontSizeOverride("font_size", 13);
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
