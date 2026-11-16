using System;
using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Interactive I/O Driver Wiring Panel (F4 key).
/// Centered modal UI allowing users to visually map PLC addresses (%I0.0, %Q0.0, OPC UA NodeIDs)
/// directly to FactoryForge component tags.
/// </summary>
public partial class DriverWiringUI : Control
{
    private TagTable? _tags;
    private readonly Dictionary<string, string> _mappings = new();
    private VBoxContainer _plcTagsList = null!;
    private VBoxContainer _simTagsList = null!;
    private Label _leftTitle = null!;
    private Label _statusLabel = null!;
    private Label _commandLabel = null!;
    private string? _selectedPlcAddress;
    private bool _useOmronAddressing = false;

    /// <summary>Where the wiring lives between sessions. <c>user://</c> is
    /// Godot's per-user data directory; the export prints the absolute path
    /// because nobody can be expected to guess where that is.</summary>
    public const string MappingPath = "user://io_mapping.json";
    public const string CsvPath = "user://io_tags.csv";

    public override void _Ready()
    {
        Visible = false;
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);

        // 1. Dark semi-transparent background overlay
        var overlay = new ColorRect
        {
            Color = new Color(0, 0, 0, 0.75f),
        };
        overlay.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(overlay);

        // 2. CenterContainer guarantees 100% viewport centering
        var centerContainer = new CenterContainer();
        centerContainer.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(centerContainer);

        // 3. Main modal card
        var modal = new PanelContainer
        {
            CustomMinimumSize = new Vector2(980, 620),
        };
        centerContainer.AddChild(modal);

        var margin = new MarginContainer();
        margin.AddThemeConstantOverride("margin_top", 24);
        margin.AddThemeConstantOverride("margin_bottom", 24);
        margin.AddThemeConstantOverride("margin_left", 24);
        margin.AddThemeConstantOverride("margin_right", 24);
        modal.AddChild(margin);

        var mainBox = new VBoxContainer();
        margin.AddChild(mainBox);

        // Header
        var header = new HBoxContainer();
        mainBox.AddChild(header);

        var title = new Label
        {
            Text = "🔌 VISUAL I/O DRIVER WIRING (F4)",
            HorizontalAlignment = HorizontalAlignment.Left,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        title.AddThemeFontSizeOverride("font_size", 20);
        header.AddChild(title);

        var closeBtn = new Button { Text = " ❌ Close " };
        closeBtn.Pressed += () => Visible = false;
        header.AddChild(closeBtn);

        var subtitle = new Label
        {
            Text = "Click a PLC Address on the left, then click a FactoryForge Tag on the right to bind them.",
        };
        subtitle.AddThemeColorOverride("font_color", new Color(0.75f, 0.75f, 0.80f));
        mainBox.AddChild(subtitle);

        mainBox.AddChild(new HSeparator());

        // Split columns
        var splitBox = new HBoxContainer { SizeFlagsVertical = SizeFlags.ExpandFill };
        mainBox.AddChild(splitBox);

        // Left Column: PLC Addresses
        var leftBox = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        splitBox.AddChild(leftBox);
        _leftTitle = new Label
        {
            Text = "⚡ PLC / DRIVER SIGNALS (%I / %Q / OPC UA)",
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        _leftTitle.AddThemeFontSizeOverride("font_size", 14);
        leftBox.AddChild(_leftTitle);

        var leftScroll = new ScrollContainer { SizeFlagsVertical = SizeFlags.ExpandFill };
        leftBox.AddChild(leftScroll);
        _plcTagsList = new VBoxContainer();
        leftScroll.AddChild(_plcTagsList);

        // Middle Divider
        splitBox.AddChild(new VSeparator());

        // Right Column: Sim Tags
        var rightBox = new VBoxContainer { SizeFlagsHorizontal = SizeFlags.ExpandFill };
        splitBox.AddChild(rightBox);
        var rightTitle = new Label
        {
            Text = "🏭 FACTORYFORGE COMPONENT TAGS",
            HorizontalAlignment = HorizontalAlignment.Center,
        };
        rightTitle.AddThemeFontSizeOverride("font_size", 14);
        rightBox.AddChild(rightTitle);

        var rightScroll = new ScrollContainer { SizeFlagsVertical = SizeFlags.ExpandFill };
        rightBox.AddChild(rightScroll);
        _simTagsList = new VBoxContainer();
        rightScroll.AddChild(_simTagsList);

        mainBox.AddChild(new HSeparator());

        // Footer status bar
        var footer = new HBoxContainer();
        mainBox.AddChild(footer);

        _statusLabel = new Label
        {
            Text = "Active Mappings: 0",
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        _statusLabel.AddThemeColorOverride("font_color", new Color(0.4f, 0.9f, 0.4f));
        footer.AddChild(_statusLabel);

        var autoMapBtn = new Button
        {
            Text = " ⚡ Auto-Map IEC (%I/%Q) ",
            TooltipText = "Give every tag in this scene an IEC address, allocated in tag order",
        };
        autoMapBtn.Pressed += AutoMapByName;
        footer.AddChild(autoMapBtn);

        var autoMapOmronBtn = new Button
        {
            Text = " ⚡ Auto-Map Omron (CIO) ",
            TooltipText = "Give every tag in this scene an Omron CX-Simulator address (CIO 0.00 / 100.00 / D0)",
        };
        autoMapOmronBtn.Pressed += AutoMapOmron;
        footer.AddChild(autoMapOmronBtn);

        var exportBtn = new Button
        {
            Text = " 💾 Export & Copy Command ",
            TooltipText = "Write io_mapping.json and io_tags.csv, and copy the sidecar command",
        };
        exportBtn.Pressed += ExportMappings;
        footer.AddChild(exportBtn);

        var clearBtn = new Button { Text = " 🗑️ Clear Mappings " };
        clearBtn.Pressed += ClearAllMappings;
        footer.AddChild(clearBtn);

        // The engine does not talk to a PLC by itself — the Python sidecar does.
        // Saying so, with the exact command, is the difference between a panel
        // that configures something and one that only looks as though it does.
        _commandLabel = new Label
        {
            Text = "Export to generate the sidecar command for this scene.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
        };
        _commandLabel.AddThemeFontSizeOverride("font_size", 11);
        _commandLabel.AddThemeColorOverride("font_color", new Color(0.70f, 0.80f, 0.95f));
        mainBox.AddChild(_commandLabel);
    }

    public void Setup(TagTable tags)
    {
        _tags = tags;
        LoadMappings();
        RebuildWiringList();
    }

    public void ToggleVisibility()
    {
        Visible = !Visible;
        if (Visible) RebuildWiringList();
    }

    public void RebuildWiringList()
    {
        if (_tags is null) return;

        foreach (Node child in _plcTagsList.GetChildren()) child.QueueFree();
        foreach (Node child in _simTagsList.GetChildren()) child.QueueFree();

        // Kolom alamat di sebelah kiri disesuaikan dengan mode addressing yang aktif
        Dictionary<string, string> suggested;
        if (_useOmronAddressing)
        {
            suggested = IoExport.SuggestOmronAddresses(_tags);
            if (_leftTitle is not null)
            {
                _leftTitle.Text = "⚡ PLC / DRIVER SIGNALS (Omron CIO / D)";
            }
        }
        else
        {
            suggested = IoExport.SuggestIecAddresses(_tags);
            if (_leftTitle is not null)
            {
                _leftTitle.Text = "⚡ PLC / DRIVER SIGNALS (%I / %Q / OPC UA)";
            }
        }

        foreach (var tag in _tags)
        {
            string addressKey = suggested.GetValueOrDefault(tag.Id, "");
            if (addressKey.Length == 0) continue;

            bool isSelected = _selectedPlcAddress == addressKey;
            string label = $"{addressKey}  ({tag.Name})";

            var btn = new Button
            {
                Text = isSelected ? $"👉 {label}" : label,
                CustomMinimumSize = new Vector2(400, 36),
                Alignment = HorizontalAlignment.Left,
                TooltipText = "Suggested address. Click, then click a tag on the right to bind it.",
            };

            btn.Pressed += () => SelectPlcAddress(addressKey);
            _plcTagsList.AddChild(btn);
        }

        // Sim Tags List
        foreach (var tag in _tags)
        {
            var row = new HBoxContainer();
            var label = new Label
            {
                Text = $"{tag.Id} [{tag.Kind}]",
                SizeFlagsHorizontal = SizeFlags.ExpandFill,
            };
            row.AddChild(label);

            bool isMapped = _mappings.ContainsKey(tag.Id);
            string mappedTo = isMapped ? $"[Mapped: {_mappings[tag.Id]}]" : "🔗 Click to Map";

            var mapBtn = new Button
            {
                Text = mappedTo,
                CustomMinimumSize = new Vector2(170, 32),
            };

            if (isMapped)
            {
                mapBtn.AddThemeColorOverride("font_color", new Color(0.3f, 1.0f, 0.4f));
            }

            string tagId = tag.Id;
            mapBtn.Pressed += () => MapSelectedToTag(tagId);
            row.AddChild(mapBtn);

            _simTagsList.AddChild(row);
        }

        UpdateStatus();
    }

    private void SelectPlcAddress(string addr)
    {
        _selectedPlcAddress = addr;
        RebuildWiringList();
        _statusLabel.Text = $"Selected PLC Address: {addr}. Click a Sim Tag map button on the right.";
    }

    private void MapSelectedToTag(string tagId)
    {
        if (string.IsNullOrEmpty(_selectedPlcAddress)) return;

        _mappings[tagId] = _selectedPlcAddress;
        RebuildWiringList();
        _statusLabel.Text = $"Mapped '{_selectedPlcAddress}' ➔ '{tagId}'";
    }

    /// <summary>
    /// Give every tag its suggested address. Derived from the live tag table, so
    /// it works on any scene — the previous version assigned a fixed list of ids
    /// from the sorting demo, most of which do not exist in a scene you built.
    /// </summary>
    private void AutoMapByName()
    {
        if (_tags is null) return;

        _useOmronAddressing = false;

        foreach (var (tagId, address) in IoExport.SuggestIecAddresses(_tags))
        {
            _mappings[tagId] = address;
        }

        RebuildWiringList();
        _statusLabel.Text = $"Auto-mapped {_mappings.Count} tags to suggested IEC addresses";
    }

    /// <summary>
    /// Memberi alamat Omron PLC (CIO 0.00 untuk input, CIO 100.00 untuk output, D0 untuk register)
    /// ke setiap tag pada scene simulasi aktif.
    /// </summary>
    private void AutoMapOmron()
    {
        if (_tags is null)
        {
            return;
        }

        _useOmronAddressing = true;

        foreach (var (tagId, address) in IoExport.SuggestOmronAddresses(_tags))
        {
            _mappings[tagId] = address;
        }

        RebuildWiringList();
        _statusLabel.Text = $"Auto-mapped {_mappings.Count} tags to suggested Omron CX-Simulator addresses";
    }

    private void ClearAllMappings()
    {
        _mappings.Clear();
        _selectedPlcAddress = null;
        RebuildWiringList();
        _statusLabel.Text = "Cleared all I/O mappings";
    }

    /// <summary>
    /// Write the wiring out where the sidecar and the person building the PLC
    /// side can both reach it.
    /// </summary>
    private void ExportMappings()
    {
        if (_tags is null) return;

        string mapPath = IoExport.WriteMappingFile(_tags, MappingPath, _mappings);
        string csvPath = IoExport.WriteTagCsv(_tags, CsvPath);

        if (mapPath.Length == 0)
        {
            _statusLabel.Text = "Export failed — see the console for the reason";
            return;
        }

        _statusLabel.Text = $"Wrote {mapPath} and {csvPath}";
        if (_useOmronAddressing)
        {
            _commandLabel.Text =
                "python -m factoryforge_sidecar connect --driver omron-cxsim " +
                $"--mapping \"{mapPath}\"";
        }
        else
        {
            _commandLabel.Text =
                "python -m factoryforge_sidecar connect --driver opcua-client " +
                $"--mapping \"{mapPath}\" -o url opc.tcp://<cpu-ip>:4840";
        }
        GD.Print($"I/O exported:\n  {mapPath}\n  {csvPath}");
        DisplayServer.ClipboardSet(_commandLabel.Text);
    }

    /// <summary>Reload mappings written by a previous session, so the wiring
    /// survives closing the program.</summary>
    private void LoadMappings()
    {
        if (!Godot.FileAccess.FileExists(MappingPath)) return;

        using var file = Godot.FileAccess.Open(MappingPath, Godot.FileAccess.ModeFlags.Read);
        string json = file?.GetAsText() ?? "";
        if (Json.ParseString(json).Obj is not Godot.Collections.Dictionary parsed) return;

        foreach (var key in parsed.Keys)
        {
            string id = key.AsString();
            if (id.StartsWith("_")) continue;

            string address = parsed[key].AsString();
            if (address.Length > 0)
            {
                _mappings[id] = address;
                if (address.StartsWith("D") || address.StartsWith("CIO") || address.StartsWith("W") || (char.IsDigit(address[0]) && address.Contains(".")))
                {
                    _useOmronAddressing = true;
                }
            }
        }

        GD.Print($"Loaded {_mappings.Count} I/O mappings from {MappingPath}");
    }

    private void UpdateStatus()
    {
        _statusLabel.Text = $"Active I/O Mappings: {_mappings.Count}";
    }
}
