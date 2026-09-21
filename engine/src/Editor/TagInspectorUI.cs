using System;
using System.Collections.Generic;
using System.Globalization;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// The live tag list: what every tag is worth right now, and the one place a
/// value can be forced by hand.
///
/// It used to be a flat list of every tag in the scene, in registration order.
/// That is fine for the eight-tag demo it was written against and unusable for
/// the twenty-nine-part library this became: the shipped accumulation buffer
/// alone publishes twenty-seven tags, and finding `stop.up` in it meant
/// scrolling past four other machines (TI-01…TI-04).
///
/// So it now does the three things anybody actually wants from a list this
/// long — **search it, group it by machine, and narrow it to the half you are
/// working on** — plus the one thing nobody wants and everybody needs: it
/// makes a *forced* tag impossible to miss. A forgotten force is a value that
/// disagrees with the simulation on purpose, and it explains more "why is my
/// program not working" than anything else in this app.
/// </summary>
public partial class TagInspectorUI : Control
{
    private TagTable _tags = null!;
    private VBoxContainer _listContainer = null!;
    private LineEdit _search = null!;
    private Button _kindBtn = null!;
    private HBoxContainer _forcedRow = null!;
    private Label _forcedLabel = null!;
    private Button _clearForcesBtn = null!;

    private readonly Dictionary<string, Label> _valueLabels = new();
    private readonly Dictionary<string, Button> _forceButtons = new();
    private readonly Dictionary<string, Button> _nameButtons = new();
    private readonly Dictionary<string, LineEdit> _valueInputs = new();

    /// <summary>Every row with the tag it shows, for filtering by visibility
    /// rather than by rebuilding — the same lesson the parts palette learned:
    /// a rebuild per keystroke destroys the search box's own focus.</summary>
    private readonly List<(Control Row, string Id, TagKind Kind)> _rows = new();

    /// <summary>Group header and body, so a group with nothing matching can
    /// step out of the way entirely.</summary>
    private readonly List<(Button Header, VBoxContainer Body, string Prefix)> _groups = new();

    /// <summary>
    /// Groups the user has collapsed by hand.
    ///
    /// Kept separately from <c>Body.Visible</c> because the filter also drives
    /// that flag, and the two mean different things: "you folded this away" and
    /// "nothing in here matches what you typed". Reading the flag as if it were
    /// the intent is what HP-40 was — a filter hid a body, clearing the filter
    /// put the header back and left the body hidden, and the header then said
    /// "▾" over nothing.
    /// </summary>
    private readonly HashSet<string> _collapsed = new();

    /// <summary>Which half of the I/O to show. The kinds are from the
    /// *controller's* point of view — an Output is what the PLC writes — which
    /// is the distinction a person mapping addresses is working along.</summary>
    private enum KindFilter { All, Outputs, Inputs }

    private KindFilter _kindFilter = KindFilter.All;

    public void Setup(TagTable tags)
    {
        _tags = tags;
        BuildUI();
    }

    private void BuildUI()
    {
        // Wider than before: the name column is the one thing a user needs to
        // read out of this panel — it is what goes into a PLC mapping file —
        // and 300px total left it truncated to "weight_readout.va…" (FF-19).
        // Widened again (360→440) for the int/float value field (UX-35): a
        // bit tag's row is name+value+force, but int/float need a typed value
        // in between, and there was no slack left for it.
        CustomMinimumSize = new Vector2(440, 400);
        SetAnchorsAndOffsetsPreset(LayoutPreset.TopRight, LayoutPresetMode.KeepSize, 20);
        // Clear of the toolbar, which is ~50px tall and was covering this
        // panel's own title. The preset's margin applies to every side at
        // once, so the top is nudged on its own rather than pushing the panel
        // in from the right edge as well.
        OffsetTop += ToolbarClearance;
        OffsetBottom += ToolbarClearance;

        var panel = new PanelContainer
        {
            CustomMinimumSize = new Vector2(440, 400),
        };
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
            Text = "TAG BUS INSPECTOR",
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        title.AddThemeFontSizeOverride("font_size", 16);
        mainBox.AddChild(title);

        var filterRow = new HBoxContainer();
        mainBox.AddChild(filterRow);

        _search = new LineEdit
        {
            PlaceholderText = "Search tags…",
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        _search.TextChanged += _ => ApplyFilter();
        filterRow.AddChild(_search);

        // One button that cycles rather than three that need explaining. The
        // panel is a fixed 440px and a row of radio buttons does not fit
        // beside a search box without squeezing the box down to uselessness.
        _kindBtn = new Button
        {
            Text = "All",
            CustomMinimumSize = new Vector2(76, 0),
            TooltipText = "All tags / only what the PLC writes / only what it reads",
        };
        _kindBtn.Pressed += CycleKindFilter;
        filterRow.AddChild(_kindBtn);

        // Hidden entirely while nothing is forced, rather than sitting there
        // as an empty line: this row is an alarm, and an alarm that is always
        // on screen is a decoration.
        _forcedRow = new HBoxContainer { Visible = false };
        mainBox.AddChild(_forcedRow);

        _forcedLabel = new Label
        {
            Text = "",
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        _forcedLabel.AddThemeColorOverride("font_color", ForcedColour);
        _forcedRow.AddChild(_forcedLabel);

        _clearForcesBtn = new Button
        {
            Text = "Release all",
            CustomMinimumSize = new Vector2(96, 0),
            TooltipText = "Hand every forced tag back to the simulation",
            Visible = false,
        };
        _clearForcesBtn.Pressed += ClearAllForces;
        _forcedRow.AddChild(_clearForcesBtn);

        var scroll = new ScrollContainer
        {
            CustomMinimumSize = new Vector2(420, 300),
            SizeFlagsVertical = SizeFlags.ExpandFill,
        };
        mainBox.AddChild(scroll);

        _listContainer = new VBoxContainer();
        scroll.AddChild(_listContainer);

        RebuildTagList();
    }

    /// <summary>Height of the scene toolbar, which this panel sits beside.</summary>
    private const float ToolbarClearance = 40.0f;

    private static readonly Color ForcedColour = new(1.0f, 0.62f, 0.25f);

    public void RebuildTagList()
    {
        if (_tags is null) return;

        foreach (var child in _listContainer.GetChildren())
        {
            child.QueueFree();
        }
        _valueLabels.Clear();
        _forceButtons.Clear();
        _nameButtons.Clear();
        _valueInputs.Clear();
        _rows.Clear();
        _groups.Clear();

        // Grouped by instance id — everything before the first dot — because
        // that prefix *is* the part, and "show me what this machine publishes"
        // is the question being asked far more often than "show me every tag
        // in registration order".
        //
        // Collected first rather than grouped on the fly. A part registers all
        // of its tags in one go today, so a run-length grouping would work by
        // luck; one interleaved pair — a renamed part re-added after another,
        // say — would give the same machine two headers and split its tags
        // between them.
        var order = new List<string>();
        var byPrefix = new Dictionary<string, List<Tag>>();

        foreach (var tag in _tags)
        {
            string prefix = PrefixOf(tag.Id);
            if (!byPrefix.TryGetValue(prefix, out var list))
            {
                list = new List<Tag>();
                byPrefix[prefix] = list;
                order.Add(prefix);
            }
            list.Add(tag);
        }

        foreach (string prefix in order)
        {
            var body = AddGroup(prefix);
            foreach (var tag in byPrefix[prefix]) AddTagRow(body, tag);
        }

        ApplyFilter();
        UpdateUI();
    }

    private static string PrefixOf(string tagId)
    {
        int dot = tagId.IndexOf('.');
        return dot > 0 ? tagId[..dot] : tagId;
    }

    private VBoxContainer AddGroup(string prefix)
    {
        var header = new Button
        {
            Text = $"▾ {prefix}",
            Alignment = HorizontalAlignment.Left,
            Flat = true,
            FocusMode = FocusModeEnum.None,
            // Deliberately not "<id>\n…": the force self-test finds a tag's row
            // by that tooltip shape, and a header that matched it would hand
            // the test a header where it wanted a row.
            TooltipText = $"Collapse or expand {prefix}",
        };
        header.AddThemeFontSizeOverride("font_size", 12);
        header.AddThemeColorOverride("font_color", new Color(0.98f, 0.80f, 0.35f));
        _listContainer.AddChild(header);

        var groupBody = new VBoxContainer();
        _listContainer.AddChild(groupBody);

        // The one place the user's own intent is recorded. Everything else
        // reads it; nothing else writes it.
        header.Pressed += () =>
        {
            if (!_collapsed.Remove(prefix)) _collapsed.Add(prefix);
            ApplyFilter();
        };

        _groups.Add((header, groupBody, prefix));
        return groupBody;
    }

    private void AddTagRow(VBoxContainer container, Tag tag)
    {
        var row = new HBoxContainer();
        container.AddChild(row);

        // A button, not a label: this is the identifier a user has to
        // retype into a PLC mapping, and click-to-copy is what makes that
        // fast instead of error-prone. Elided in the *middle* rather than
        // Godot's own end-truncation — the suffix (".value", ".detect")
        // is what tells two similarly-prefixed tags apart, so cutting it
        // off is exactly backwards. See FF-19.
        var nameBtn = new Button
        {
            Text = ElideMiddle(tag.Id, 20),
            CustomMinimumSize = new Vector2(170, 0),
            Alignment = HorizontalAlignment.Left,
            Flat = true,
            TooltipText = $"{tag.Id}\n{tag.Type} · {tag.Kind}\nClick to copy",
        };
        string fullId = tag.Id;
        nameBtn.Pressed += () =>
        {
            DisplayServer.ClipboardSet(fullId);
            GD.Print($"copied tag id: {fullId}");
        };
        row.AddChild(nameBtn);
        _nameButtons[tag.Id] = nameBtn;

        var valLabel = new Label
        {
            Text = _tags.Visible(tag.Id)?.ToString() ?? "null",
            CustomMinimumSize = new Vector2(60, 0),
        };
        row.AddChild(valLabel);
        _valueLabels[tag.Id] = valLabel;

        // Bit tags force with one click (there is only one other value).
        // Int/float need a typed target first — the plumbing has always
        // accepted any object (TagTable.Force takes object, and parts
        // read through TryGetVisible), so this was a UI gap only (UX-35).
        LineEdit? valueInput = null;
        if (tag.Type != TagType.Bit)
        {
            valueInput = new LineEdit
            {
                Text = FormatValue(tag.Type, _tags.Visible(tag.Id)),
                CustomMinimumSize = new Vector2(70, 0),
                TooltipText = $"Type a {Tag.TypeName(tag.Type)} value, then press Force.",
            };
            row.AddChild(valueInput);
            _valueInputs[tag.Id] = valueInput;
        }

        bool isForced = _tags.IsForced(tag.Id);
        var forceBtn = new Button
        {
            Text = isForced ? "UNFORCE" : "Force",
            CustomMinimumSize = new Vector2(60, 0),
        };
        string tagId = tag.Id;
        forceBtn.Pressed += () => ToggleForce(tagId, valueInput);
        row.AddChild(forceBtn);
        _forceButtons[tagId] = forceBtn;

        _rows.Add((row, tag.Id, tag.Kind));
    }

    private void CycleKindFilter()
    {
        _kindFilter = _kindFilter switch
        {
            KindFilter.All => KindFilter.Outputs,
            KindFilter.Outputs => KindFilter.Inputs,
            _ => KindFilter.All,
        };
        _kindBtn.Text = _kindFilter switch
        {
            KindFilter.Outputs => "PLC → ",
            KindFilter.Inputs => " → PLC",
            _ => "All",
        };
        ApplyFilter();
    }

    /// <summary>
    /// Show the rows that match, hide the rest, and take an empty group's
    /// header out of the way with it.
    ///
    /// By visibility, never by rebuilding: rebuilding recreates the search
    /// box's siblings under it and costs focus on every keystroke, which makes
    /// a search box that cannot be typed into. The palette learned this the
    /// hard way (CP-20) and this is the same shape of panel.
    /// </summary>
    private void ApplyFilter()
    {
        string needle = _search.Text.Trim().ToLowerInvariant();
        bool searching = needle.Length > 0;

        var matchesInGroup = new Dictionary<string, int>();

        foreach (var (row, id, kind) in _rows)
        {
            bool kindOk = _kindFilter switch
            {
                KindFilter.Outputs => kind == TagKind.Output,
                KindFilter.Inputs => kind == TagKind.Input,
                _ => true,
            };
            bool textOk = !searching || id.ToLowerInvariant().Contains(needle);
            bool show = kindOk && textOk;

            row.Visible = show;
            if (!show) continue;

            string prefix = PrefixOf(id);
            matchesInGroup[prefix] = matchesInGroup.GetValueOrDefault(prefix) + 1;
        }

        bool filtering = searching || _kindFilter != KindFilter.All;

        foreach (var (header, body, prefix) in _groups)
        {
            int matches = matchesInGroup.GetValueOrDefault(prefix);
            header.Visible = matches > 0;

            // A collapsed group would hide its own matches while filtering, so
            // filtering forces every surviving group open. Collapsing is a
            // thing you do to a *full* list.
            //
            // The branch that used to be missing is the third one: with no
            // filter running, the body goes back to whatever the user last
            // chose. Before HP-40 there was no such case at all -- a group the
            // filter had hidden simply stayed hidden when the search was
            // cleared, under a header that had reappeared and still read "▾".
            // Every group body in the panel could be emptied that way by typing
            // one machine's name and pressing backspace.
            bool open = matches > 0 && (filtering || !_collapsed.Contains(prefix));
            body.Visible = open;
            header.Text = (open ? "▾ " : "▸ ") + prefix;
        }
    }

    private void ClearAllForces()
    {
        if (_tags is null) return;

        var forced = new List<string>();
        foreach (var tag in _tags)
        {
            if (_tags.IsForced(tag.Id)) forced.Add(tag.Id);
        }
        foreach (string id in forced) _tags.ClearForce(id);

        if (forced.Count > 0)
            GD.Print($"released {forced.Count} forced tag(s) back to the simulation");
        UpdateUI();
    }

    private static string FormatValue(TagType type, object? value) => type switch
    {
        TagType.Float => value is null ? "0" : Convert.ToDouble(value).ToString("0.###", CultureInfo.InvariantCulture),
        _ => value?.ToString() ?? "0",
    };

    /// <summary>Shorten to at most <paramref name="maxChars"/>, cutting from
    /// the middle so the id's discriminating suffix survives.</summary>
    private static string ElideMiddle(string id, int maxChars)
    {
        if (id.Length <= maxChars) return id;
        int keep = maxChars - 1;   // one char reserved for the ellipsis glyph
        int left = keep * 2 / 5;   // favour the suffix: it is what differs
        int right = keep - left;
        return id[..left] + "…" + id[^right..];
    }

    private void ToggleForce(string tagId, LineEdit? valueInput)
    {
        var tag = _tags.Get(tagId);
        if (tag is null) return;

        if (_tags.IsForced(tagId))
        {
            _tags.ClearForce(tagId);
        }
        else if (tag.Type == TagType.Bit)
        {
            bool current = (bool)_tags.Visible(tagId);
            _tags.Force(tagId, !current);
        }
        else if (valueInput is not null)
        {
            if (!TryParse(tag.Type, valueInput.Text, out object parsed))
            {
                FlashInvalid(valueInput);
                return;
            }
            _tags.Force(tagId, parsed);
        }
        UpdateUI();
    }

    private static bool TryParse(TagType type, string text, out object value)
    {
        text = text.Trim();
        if (type == TagType.Int && int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out int i))
        {
            value = i;
            return true;
        }
        if (type == TagType.Float && double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out double d))
        {
            value = d;
            return true;
        }
        value = 0;
        return false;
    }

    /// <summary>A bad value refuses audibly instead of doing nothing (UX-36):
    /// the field's text flashes red for a second.</summary>
    private void FlashInvalid(LineEdit input)
    {
        input.AddThemeColorOverride("font_color", new Color(1f, 0.4f, 0.4f));
        GetTree().CreateTimer(1.0).Timeout += () => input.RemoveThemeColorOverride("font_color");
    }

    public override void _Process(double delta)
    {
        UpdateUI();
    }

    private void UpdateUI()
    {
        if (_tags is null) return;

        int forcedCount = 0;

        foreach (var tag in _tags)
        {
            bool forced = _tags.IsForced(tag.Id);
            if (forced) forcedCount++;

            if (_valueLabels.TryGetValue(tag.Id, out var valLabel))
            {
                valLabel.Text = _tags.Visible(tag.Id)?.ToString() ?? "null";
            }
            if (_forceButtons.TryGetValue(tag.Id, out var forceBtn))
            {
                forceBtn.Text = forced ? "UNFORCE" : "Force";
            }
            // A forced tag is a value that disagrees with the simulation on
            // purpose. The button already said so if you looked at it; the
            // *name* says so now, because the failure mode is forgetting a
            // force exists rather than being unable to find it (TI-04).
            if (_nameButtons.TryGetValue(tag.Id, out var nameBtn))
            {
                if (forced) nameBtn.AddThemeColorOverride("font_color", ForcedColour);
                else nameBtn.RemoveThemeColorOverride("font_color");
            }
            // Track the live value while the user isn't typing or holding a
            // force — once forced, the field shows exactly what was forced,
            // undisturbed, until UNFORCE is pressed.
            if (_valueInputs.TryGetValue(tag.Id, out var input) &&
                !input.HasFocus() && !forced)
            {
                input.Text = FormatValue(tag.Type, _tags.Visible(tag.Id));
            }
        }

        if (_forcedLabel is not null)
        {
            _forcedLabel.Text = forcedCount == 0
                ? ""
                : $"{forcedCount} tag{(forcedCount == 1 ? "" : "s")} forced by hand";
        }
        if (_clearForcesBtn is not null) _clearForcesBtn.Visible = forcedCount > 0;
        if (_forcedRow is not null) _forcedRow.Visible = forcedCount > 0;
    }
}
