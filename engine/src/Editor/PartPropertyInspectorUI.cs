using System;
using System.Collections.Generic;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// UI Inspector panel for viewing and editing live properties of a selected 3D component.
/// </summary>
public partial class PartPropertyInspectorUI : Control, IPartInspector
{
    private VBoxContainer _contentContainer = null!;
    private ScrollContainer _scroll = null!;
    private Node3D? _selectedNode;

    /// <summary>One entry per live tag control currently on screen, run every
    /// frame to keep readouts and un-touched controls tracking the simulation
    /// (UX-34). Cleared and rebuilt on every selection change.</summary>
    private readonly List<Action> _liveRefreshers = new();

    /// <summary>The editor that owns the selection, so the name field can act on
    /// it. Set by Main; null in headless builds, where the inspector never
    /// exists.</summary>
    public SceneEditor? Editor { get; set; }

    public override void _Ready()
    {
        CustomMinimumSize = new Vector2(280, 240);
        SetAnchorsAndOffsetsPreset(LayoutPreset.BottomRight, LayoutPresetMode.KeepSize, 20);

        var panel = new PanelContainer
        {
            CustomMinimumSize = new Vector2(280, 240),
        };
        AddChild(panel);

        var margin = new MarginContainer();
        margin.AddThemeConstantOverride("margin_top", 8);
        margin.AddThemeConstantOverride("margin_bottom", 8);
        margin.AddThemeConstantOverride("margin_left", 8);
        margin.AddThemeConstantOverride("margin_right", 8);
        panel.AddChild(margin);

        var mainBox = new VBoxContainer();
        margin.AddChild(mainBox);

        var title = new Label
        {
            Text = "PART PROPERTIES",
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        title.AddThemeFontSizeOverride("font_size", 14);
        mainBox.AddChild(title);

        // Bounded and scrollable, not just a VBoxContainer straight in mainBox:
        // the empty state now carries a template's title and blurb (UX-32), and
        // the longest one wraps to more lines than the fixed-height panel has
        // room for. A plain Control's minimum size does not propagate to its
        // parent (that is what broke the F5 dialog once already), so without a
        // hard bound here a long blurb grows the panel past its anchored
        // position instead of just scrolling.
        _scroll = new ScrollContainer
        {
            CustomMinimumSize = new Vector2(260, 190),
            SizeFlagsVertical = SizeFlags.ExpandFill,
            HorizontalScrollMode = ScrollContainer.ScrollMode.Disabled,
        };
        mainBox.AddChild(_scroll);

        _contentContainer = new VBoxContainer();
        _scroll.AddChild(_contentContainer);

        ShowNoSelection();
    }

    public override void _Process(double delta)
    {
        foreach (var refresh in _liveRefreshers) refresh();
    }

    public void InspectNode(Node3D? node, string instanceId, string partType)
    {
        _selectedNode = node;
        _liveRefreshers.Clear();
        foreach (var child in _contentContainer.GetChildren())
        {
            // RemoveChild first: QueueFree alone defers to the end of the frame,
            // so the panel would briefly hold the old part's rows and the new
            // one's at once. The parts' own Rebuild() methods use the same pair
            // for the same reason.
            _contentContainer.RemoveChild(child);
            child.QueueFree();
        }

        if (node is null)
        {
            ShowNoSelection();
            return;
        }

        var info = PartCatalog.Find(partType);

        var header = new Label { Text = info?.Label ?? partType };
        header.AddThemeFontSizeOverride("font_size", 13);
        _contentContainer.AddChild(header);

        // What this part is for, from the same catalog the palette's tooltip
        // reads (CP-22). Selecting a part used to show its class name and a
        // list of sliders, which answers "what can I change" and never "what is
        // this" — and the class name is not even the thing the user clicked,
        // since the palette calls it something friendlier.
        if (info is not null)
        {
            var summary = new Label
            {
                Text = info.Summary,
                AutowrapMode = TextServer.AutowrapMode.WordSmart,
                CustomMinimumSize = new Vector2(240, 0),
            };
            summary.AddThemeFontSizeOverride("font_size", 10);
            summary.AddThemeColorOverride("font_color", new Color(0.70f, 0.75f, 0.82f));
            _contentContainer.AddChild(summary);
        }

        // What you can do with the thing you just selected, where you are
        // already looking. Every one of these worked before OP-09 and none of
        // them was written down anywhere in the app: the first thing everybody
        // tries is dragging the part, which did nothing, and the key that did
        // work (M) was mentioned nowhere on screen.
        var affordances = new Label
        {
            Text = "Drag to move  ·  R rotate  ·  Ctrl+D duplicate  ·  Del delete",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            CustomMinimumSize = new Vector2(240, 0),
        };
        affordances.AddThemeFontSizeOverride("font_size", 10);
        affordances.AddThemeColorOverride("font_color", new Color(0.60f, 0.65f, 0.72f));
        _contentContainer.AddChild(affordances);

        AddNameRow(instanceId);

        // The part describes its own rows (HP-34). This used to be a
        // twenty-three branch `else if` chain on the node's concrete type, in a
        // file that could not see the fields it was building sliders for, and
        // whose rule -- "anything read only while the part is built needs a
        // Rebuild() alongside it" -- had to be remembered by whoever edited it
        // rather than by whoever wrote the part.
        //
        // The rule has not changed; it has only moved next to the setter it
        // applies to.
        _inspectedInstanceId = instanceId;
        if (node is IPart part) part.DescribeControls(this);

        AddTagControlsSection(instanceId);
        ResetScroll();
    }

    /// <summary>Back to the top on every selection change, so switching from a
    /// part you had scrolled down on to a new one never opens already
    /// scrolled past its settings. Deferred: the container has not
    /// re-measured the new content's height until after this frame.</summary>
    private void ResetScroll() => CallDeferred(nameof(ScrollToTop));
    private void ScrollToTop() => _scroll.ScrollVertical = 0;

    /// <summary>
    /// The part's own I/O, right below its settings (UX-34): a toggle for a
    /// bit output, a slider or spin box for an int/float output, and a live
    /// readout with an override toggle for an input. This is what used to be
    /// three indirections away -- know the tag suffix, find it in a
    /// 13-16-row list on the other side of the screen, press Force there
    /// (§2.9) -- now sitting under the part you already have selected.
    /// </summary>
    private void AddTagControlsSection(string instanceId)
    {
        var tags = Editor?.Tags;
        if (tags is null) return;

        var ownTags = new List<Tag>();
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(instanceId + ".", StringComparison.Ordinal)) ownTags.Add(tag);
        }
        if (ownTags.Count == 0) return;

        // TagTable enumerates a Dictionary, which is not insertion order --
        // outputs first (the controls a hand actually reaches for) then
        // inputs, each alphabetised, so the row order is at least
        // deterministic across runs rather than whatever the hash happened
        // to produce.
        ownTags.Sort((a, b) =>
        {
            int byKind = (a.Kind == TagKind.Output ? 0 : 1).CompareTo(b.Kind == TagKind.Output ? 0 : 1);
            return byKind != 0 ? byKind : string.CompareOrdinal(a.Id, b.Id);
        });

        _contentContainer.AddChild(new HSeparator());
        var header = new Label { Text = "I/O", HorizontalAlignment = HorizontalAlignment.Center };
        header.AddThemeFontSizeOverride("font_size", 12);
        _contentContainer.AddChild(header);

        foreach (var tag in ownTags)
        {
            if (tag.Kind == TagKind.Output) AddOutputRow(tags, tag);
            else AddInputRow(tags, tag);
        }
    }

    private static string Suffix(string tagId) => tagId[(tagId.IndexOf('.') + 1)..];

    /// <summary>
    /// An output is something this panel already gets to drive directly --
    /// Force, the same call the Tag Inspector's own button makes, so "forcing
    /// is sticky" and an UNFORCE-style release stays true here too (§5.2).
    /// </summary>
    private void AddOutputRow(TagTable tags, Tag tag)
    {
        string suffix = Suffix(tag.Id);
        var row = new HBoxContainer();
        _contentContainer.AddChild(row);
        row.AddChild(new Label { Text = suffix, CustomMinimumSize = new Vector2(70, 0), TooltipText = tag.Id });

        if (tag.Type == TagType.Bit && suffix == "emit")
        {
            // A rising edge, not a level: holding this true streams nothing
            // new (the feed's own dispatch only spawns on the edge), so
            // a plain toggle left on would look broken. One press, one box.
            var btn = new Button { Text = "Emit one", CustomMinimumSize = new Vector2(90, 0) };
            btn.Pressed += () =>
            {
                tags.Force(tag.Id, true);
                GetTree().CreateTimer(0.05).Timeout += () => { if (tags.Contains(tag.Id)) tags.ClearForce(tag.Id); };
            };
            row.AddChild(btn);
            return;
        }

        if (tag.Type == TagType.Bit)
        {
            var toggle = new CheckButton();
            toggle.SetPressedNoSignal(Convert.ToBoolean(tags.Visible(tag.Id)));
            toggle.Toggled += (on) => tags.Force(tag.Id, on);
            row.AddChild(toggle);
            _liveRefreshers.Add(() =>
            {
                if (tags.IsForced(tag.Id) || toggle.HasFocus()) return;
                toggle.SetPressedNoSignal(Convert.ToBoolean(tags.Visible(tag.Id)));
            });
        }
        else if (tag.Type == TagType.Int)
        {
            var spin = new SpinBox
            {
                MinValue = 0, MaxValue = 9999, Step = 1,
                CustomMinimumSize = new Vector2(90, 0),
            };
            _initializing = true;
            spin.Value = Convert.ToDouble(tags.Visible(tag.Id));
            _initializing = false;
            spin.ValueChanged += (val) => { if (!_initializing) tags.Force(tag.Id, (int)val); };
            row.AddChild(spin);
            _liveRefreshers.Add(() =>
            {
                if (tags.IsForced(tag.Id) || spin.HasFocus()) return;
                _initializing = true;
                spin.Value = Convert.ToDouble(tags.Visible(tag.Id));
                _initializing = false;
            });
        }
        else // Float — a slider, not a field: "drag the fill valve, watch the level rise" (§5.4).
        {
            var slider = new HSlider
            {
                MinValue = 0, MaxValue = 100, Step = 0.5,
                CustomMinimumSize = new Vector2(120, 0),
            };
            _initializing = true;
            slider.Value = Convert.ToDouble(tags.Visible(tag.Id));
            _initializing = false;
            slider.ValueChanged += (val) => { if (!_initializing) tags.Force(tag.Id, val); };
            row.AddChild(slider);
            _liveRefreshers.Add(() =>
            {
                if (tags.IsForced(tag.Id) || slider.HasFocus()) return;
                _initializing = true;
                slider.Value = Convert.ToDouble(tags.Visible(tag.Id));
                _initializing = false;
            });
        }
    }

    /// <summary>
    /// An input is the simulation's own: a sensor, a counter, a measurement.
    /// Shown read-only by default, with an override that forces it exactly
    /// the way the Tag Inspector would -- "what does my PLC do if this sensor
    /// is stuck on?" (§5.4) -- without needing to leave this panel to ask it.
    /// </summary>
    private void AddInputRow(TagTable tags, Tag tag)
    {
        string suffix = Suffix(tag.Id);
        var row = new HBoxContainer();
        _contentContainer.AddChild(row);
        row.AddChild(new Label { Text = suffix, CustomMinimumSize = new Vector2(70, 0), TooltipText = tag.Id });

        var readout = new Label { Text = FormatValue(tag, tags.Visible(tag.Id)), CustomMinimumSize = new Vector2(60, 0) };
        row.AddChild(readout);

        var overrideBox = new CheckBox { Text = "Override", ButtonPressed = false };
        row.AddChild(overrideBox);

        Control? liveControl = null;

        overrideBox.Toggled += (on) =>
        {
            if (on)
            {
                tags.Force(tag.Id, tags.Visible(tag.Id));
                liveControl = AddOverrideControl(row, tags, tag);
            }
            else
            {
                tags.ClearForce(tag.Id);
                liveControl?.QueueFree();
                liveControl = null;
            }
        };

        _liveRefreshers.Add(() =>
        {
            if (!tags.Contains(tag.Id)) return;
            if (overrideBox.ButtonPressed) return;   // the override control shows the held value instead
            readout.Text = FormatValue(tag, tags.Visible(tag.Id));
        });
    }

    /// <summary>The same bit/int/float control an output gets, added once an
    /// input's override is switched on, wired to Force instead of read-only.</summary>
    private Control AddOverrideControl(HBoxContainer row, TagTable tags, Tag tag)
    {
        if (tag.Type == TagType.Bit)
        {
            var toggle = new CheckButton();
            toggle.SetPressedNoSignal(Convert.ToBoolean(tags.Visible(tag.Id)));
            toggle.Toggled += (on) => tags.Force(tag.Id, on);
            row.AddChild(toggle);
            return toggle;
        }
        if (tag.Type == TagType.Int)
        {
            var spin = new SpinBox { MinValue = 0, MaxValue = 9999, Step = 1, CustomMinimumSize = new Vector2(90, 0) };
            _initializing = true;
            spin.Value = Convert.ToDouble(tags.Visible(tag.Id));
            _initializing = false;
            spin.ValueChanged += (val) => { if (!_initializing) tags.Force(tag.Id, (int)val); };
            row.AddChild(spin);
            return spin;
        }
        // Float inputs span very different scales (a level in percent, a
        // height in metres) with no shared unit to assume, unlike the fill/drain
        // outputs above which are always percent. Scaling the range off the
        // live value at least keeps a metre-scale reading from being squeezed
        // into the first 1% of a 0-100 slider.
        double current = Convert.ToDouble(tags.Visible(tag.Id));
        double max = Math.Abs(current) <= 2.0 ? 2.0 : 100.0;
        var slider = new HSlider
        {
            MinValue = 0, MaxValue = max, Step = max <= 2.0 ? 0.01 : 0.5,
            CustomMinimumSize = new Vector2(120, 0),
        };
        _initializing = true;
        slider.Value = current;
        _initializing = false;
        slider.ValueChanged += (val) => { if (!_initializing) tags.Force(tag.Id, val); };
        row.AddChild(slider);
        return slider;
    }

    private static string FormatValue(Tag tag, object? value) => tag.Type switch
    {
        TagType.Float => value is null ? "0" : Convert.ToDouble(value).ToString("0.###"),
        _ => value?.ToString() ?? "0",
    };

    /// <summary>Guards the handful of ValueChanged wires above against firing
    /// on their own initial value assignment -- Range controls (unlike
    /// BaseButton) have no SetValueNoSignal, so this is the only way to set a
    /// slider or spin box's starting position without that alone forcing the
    /// tag.</summary>
    private bool _initializing;

    /// <summary>
    /// The part's id, editable. This is the string that ends up in a mapping
    /// file and in the PLC program, so it is worth being able to write
    /// "reject_pusher" instead of living with "pushermechanism_2".
    ///
    /// Committing on Enter only, never on every keystroke: renaming per
    /// character would fire a rename for "r", "re", "rej"… each one moving the
    /// tags again.
    /// </summary>
    private void AddNameRow(string instanceId)
    {
        var row = new HBoxContainer();
        _contentContainer.AddChild(row);
        row.AddChild(new Label { Text = "Name", CustomMinimumSize = new Vector2(46, 0) });

        var field = new LineEdit
        {
            Text = instanceId,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            TooltipText = "Tag prefix for this part. Press Enter to apply.",
        };
        row.AddChild(field);

        var status = new Label { Text = "" };
        status.AddThemeFontSizeOverride("font_size", 11);
        _contentContainer.AddChild(status);

        field.TextSubmitted += (text) =>
        {
            if (Editor is null) return;

            if (Editor.TryRenameSelectedPart(text, out string problem))
            {
                status.AddThemeColorOverride("font_color", new Color(0.45f, 0.95f, 0.55f));
                status.Text = $"renamed to {text.Trim()}";
            }
            else
            {
                // Put the old name back, so the field never shows an id the
                // scene does not actually have.
                field.Text = instanceId;
                status.AddThemeColorOverride("font_color", new Color(1.0f, 0.55f, 0.45f));
                status.Text = problem;
            }
        };
    }

    /// <summary>The manifest, loaded once — this panel only ever reads it to
    /// find the loaded scene's own blurb, never to list templates.</summary>
    private static readonly IReadOnlyList<TemplateEntry> Templates = TemplateManifest.Load();

    /// <summary>
    /// Re-show the empty state, picking up whatever the loaded scene now is.
    /// Called after a scene finishes loading (UX-32): the template's blurb
    /// used to live only on the start screen and vanish the moment you picked
    /// one, so "what does this scene teach" was unanswerable without going
    /// Home and reading it again. A part still selected is left alone.
    /// </summary>
    public void RefreshIfEmpty()
    {
        if (_selectedNode is null) ShowNoSelection();
    }

    private void ShowNoSelection()
    {
        foreach (var child in _contentContainer.GetChildren())
        {
            child.QueueFree();
        }

        string? sceneName = Editor?.SceneName;
        TemplateEntry? current = null;
        foreach (var t in Templates)
        {
            if (t.Scene == sceneName) { current = t; break; }
        }

        if (current is { } entry)
        {
            var heading = new Label { Text = entry.Title };
            heading.AddThemeFontSizeOverride("font_size", 13);
            _contentContainer.AddChild(heading);

            _contentContainer.AddChild(new Label
            {
                Text = entry.Blurb,
                AutowrapMode = TextServer.AutowrapMode.WordSmart,
                CustomMinimumSize = new Vector2(260, 0),
            });
            _contentContainer.AddChild(new HSeparator());
        }

        // Wrapped, not clipped: the panel is anchored to the bottom-right, so an
        // unwrapped label wider than the panel pushes the whole thing off the
        // edge of the screen and takes its own last words with it.
        _contentContainer.AddChild(new Label
        {
            Text = "Click a placed part to inspect and rename it.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            CustomMinimumSize = new Vector2(260, 0),
        });
        ResetScroll();
    }

    // ---------- IPartInspector (HP-34)

    /// <summary>The part currently being described, so a row that has to name
    /// one of its own tags can build the id.</summary>
    private string _inspectedInstanceId = "";

    string IPartInspector.InstanceId => _inspectedInstanceId;

    void IPartInspector.Slider(string label, float value, float min, float max, float step,
                               Action<float> onChanged) =>
        AddSliderProperty(label, value, min, max, step, onChanged);

    void IPartInspector.Text(string label, string value, int maxLength,
                             Action<string> onChanged) =>
        AddTextProperty(label, value, maxLength, onChanged);

    /// <summary>
    /// A dropdown of the <c>int</c> input tags the scene actually has, never a
    /// free-text field: the value is published through
    /// <see cref="TagTable.TrySet"/>, which ignores an id nothing owns, so a
    /// typo would be a silent no-op — a new one, in the panel built to remove
    /// them (LE-04).
    /// </summary>
    void IPartInspector.TagPicker(string label, string current, string ownTagId,
                                  Action<string> onChanged)
    {
        var tags = Editor?.Tags;
        if (tags is null) return;

        var options = new List<string> { ownTagId };
        foreach (var tag in tags)
        {
            if (tag.Type == TagType.Int && tag.Kind == TagKind.Input && tag.Id != ownTagId)
                options.Add(tag.Id);
        }

        // A scene file can point a part at a tag that no longer exists. Show it
        // rather than silently snapping the selection to something else.
        if (!options.Contains(current)) options.Insert(1, current);

        var row = new HBoxContainer();
        _contentContainer.AddChild(row);
        row.AddChild(new Label { Text = label, CustomMinimumSize = new Vector2(100, 0) });

        // FitToLongestItem off, ClipText on, and a fixed minimum. An
        // OptionButton otherwise takes the width of its longest *menu item*,
        // not its current one — measured at 316px against the 234-240px every
        // other row asks for, which pushed this fixed-width panel off the right
        // of the screen. Same failure the F5 dialog had once, and the reason the
        // empty state below sits in a bounded ScrollContainer. C21 now asserts
        // every row fits, so the next one cannot do it quietly.
        var picker = new OptionButton
        {
            CustomMinimumSize = new Vector2(150, 0),
            FitToLongestItem = false,
            ClipText = true,
            TooltipText = $"Which tag this part writes its {label.ToLowerInvariant()} value into.",
        };
        for (int i = 0; i < options.Count; i++)
        {
            picker.AddItem(options[i] == ownTagId ? $"{ownTagId} (own)" : options[i], i);
            if (options[i] == current) picker.Selected = i;
        }
        picker.ItemSelected += index =>
        {
            onChanged(options[(int)index]);
            Editor?.MarkDirty();
        };
        row.AddChild(picker);
    }

    /// <summary>A short free-text setting. Only the setpoint plate's unit uses
    /// this so far — it is a caption, not a value, and there is no sensible
    /// slider for "g".</summary>
    private void AddTextProperty(string labelText, string initialValue, int maxLength,
                                 System.Action<string> onChanged)
    {
        var row = new HBoxContainer();
        _contentContainer.AddChild(row);

        row.AddChild(new Label { Text = labelText, CustomMinimumSize = new Vector2(140, 0) });

        var field = new LineEdit
        {
            Text = initialValue,
            MaxLength = maxLength,
            CustomMinimumSize = new Vector2(90, 0),
            // Without this the field takes its width from its content and a
            // long entry grows the row past the panel's scroll bound, the same
            // way the tag dropdown did (LE-04).
            ExpandToTextLength = false,
        };
        field.TextChanged += (text) => { onChanged(text); Editor?.MarkDirty(); };
        row.AddChild(field);
    }

    private void AddSliderProperty(string labelText, float initialValue, float min, float max, float step, System.Action<float> onChanged)
    {
        var row = new HBoxContainer();
        _contentContainer.AddChild(row);

        var label = new Label { Text = labelText, CustomMinimumSize = new Vector2(140, 0) };
        row.AddChild(label);

        var spin = new SpinBox
        {
            MinValue = min,
            MaxValue = max,
            Step = step,
            Value = initialValue,
            CustomMinimumSize = new Vector2(90, 0),
        };
        spin.ValueChanged += (val) => { onChanged((float)val); Editor?.MarkDirty(); };
        row.AddChild(spin);
    }
}
