using System;
using System.Net.Sockets;
using System.Threading.Tasks;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Interactive Driver Connection Selector Modal UI (F5 Key or Toolbar).
/// Features Auto-Detect Mode to automatically probe and identify active PLCs (PLCSIM Advanced, OPC UA, S7 ISO-on-TCP, Modbus).
/// </summary>
public partial class DriverConnectionUI : Control
{
    private OptionButton _driverDropdown = null!;
    private Label _commandLabel = null!;

    /// <summary>Process id of the sidecar started from this dialog, so it can be
    /// stopped and is not left holding a PLC session when the window closes.</summary>
    private int _sidecarPid;
    private Label _ipLabel = null!;
    private LineEdit _ipInput = null!;
    private Label _portLabel = null!;
    private LineEdit _portInput = null!;
    private Label _instanceLabel = null!;
    private LineEdit _instanceInput = null!;
    private Label _dbLabel = null!;
    private LineEdit _dbInput = null!;
    private Label _modbusHostLabel = null!;
    private LineEdit _modbusHostInput = null!;
    private Label _modbusPortLabel = null!;
    private LineEdit _modbusPortInput = null!;
    private Label _bindNote = null!;
    private PanelContainer _omronInfoCard = null!;
    private Label _omronStatusLabel = null!;
    private Label _statusLabel = null!;

    /// <summary>Set by Main so the empty-state offer (UX-33) can name and run
    /// the loaded scene's exercise.</summary>
    public SceneEditor? Editor { get; set; }
    public IdleHintUI? IdleHint { get; set; }

    /// <summary>Internal set so <c>--self-test=sidecar</c> can build a
    /// command for a chosen driver without a dropdown.</summary>
    public string SelectedDriver { get; internal set; } = "plcsim-advanced";
    public string IpAddress { get; private set; } = "";
    public string PortOrUrl { get; private set; } = "4840";
    public string InstanceName { get; private set; } = "";
    public int DbNumber { get; private set; } = 1;

    /// <summary>The address the Modbus server binds (IP-37). Loopback by
    /// default, as the driver's own default is (HP-22): Modbus has no
    /// authentication, so serving it to the network is something the user
    /// types, never something the dialog assumes. OpenPLC in WSL, a VM or on
    /// another machine cannot reach 127.0.0.1, which is why it is a field at
    /// all.</summary>
    public string ModbusHost { get; private set; } = DefaultModbusHost;
    public int ModbusPort { get; private set; } = DefaultModbusPort;

    public const string DefaultModbusHost = "127.0.0.1";
    public const int DefaultModbusPort = 502;

    private const string SettingsPath = "user://driver_connection.cfg";

    /// <summary>Remember whatever the user last typed, since there is no sane
    /// global default for someone else's PLC address or instance name.</summary>
    private void LoadLastSettings()
    {
        var cfg = new ConfigFile();
        if (cfg.Load(SettingsPath) != Error.Ok) return;
        IpAddress = (string)cfg.GetValue("driver", "ip", IpAddress);
        PortOrUrl = (string)cfg.GetValue("driver", "port_or_url", PortOrUrl);
        InstanceName = (string)cfg.GetValue("driver", "instance", InstanceName);
        DbNumber = (int)cfg.GetValue("driver", "db", DbNumber);
        // Through the validator, so a hand-edited or stale file cannot put a
        // value into the command that the form itself would have refused.
        TryApplyModbusBind((string)cfg.GetValue("driver", "modbus_host", ModbusHost),
                           cfg.GetValue("driver", "modbus_port", ModbusPort).ToString(),
                           out _);
    }

    private void SaveLastSettings()
    {
        var cfg = new ConfigFile();
        cfg.SetValue("driver", "ip", IpAddress);
        cfg.SetValue("driver", "port_or_url", PortOrUrl);
        cfg.SetValue("driver", "instance", InstanceName);
        cfg.SetValue("driver", "db", DbNumber);
        cfg.SetValue("driver", "modbus_host", ModbusHost);
        cfg.SetValue("driver", "modbus_port", ModbusPort);
        cfg.Save(SettingsPath);
    }

    public override void _Ready()
    {
        LoadLastSettings();
        Visible = false;
        SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);

        // Dark background overlay
        var overlay = new ColorRect
        {
            Color = new Color(0, 0, 0, 0.75f),
        };
        overlay.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(overlay);

        // Center modal container
        var centerContainer = new CenterContainer();
        centerContainer.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        AddChild(centerContainer);

        var modal = new PanelContainer
        {
            CustomMinimumSize = new Vector2(680, 520),
        };
        centerContainer.AddChild(modal);

        var margin = new MarginContainer();
        margin.AddThemeConstantOverride("margin_top", 20);
        margin.AddThemeConstantOverride("margin_bottom", 20);
        margin.AddThemeConstantOverride("margin_left", 20);
        margin.AddThemeConstantOverride("margin_right", 20);
        modal.AddChild(margin);

        var mainBox = new VBoxContainer();
        margin.AddChild(mainBox);

        // Header
        var header = new HBoxContainer();
        mainBox.AddChild(header);

        var title = new Label
        {
            Text = "🔌 SELECT PLC DRIVER & CONNECTION (F5)",
            HorizontalAlignment = HorizontalAlignment.Left,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        title.AddThemeFontSizeOverride("font_size", 18);
        header.AddChild(title);

        var autoDetectHeaderBtn = new Button { Text = " 🔍 Auto-Detect Mode " };
        autoDetectHeaderBtn.Pressed += () => _ = RunAutoDetectAsync();
        header.AddChild(autoDetectHeaderBtn);

        var closeBtn = new Button { Text = " ❌ Close " };
        closeBtn.Pressed += () => Visible = false;
        header.AddChild(closeBtn);

        mainBox.AddChild(new HSeparator());

        // UX-33: the first thing a fresh open of this dialog offers, ahead of
        // any driver configuration -- "no PLC yet" is the normal state for
        // someone who has never touched TIA Portal, not a problem to route
        // around straight into IP addresses and instance names.
        var exerciseRow = new HBoxContainer();
        mainBox.AddChild(exerciseRow);

        var exerciseLabel = new Label
        {
            Text = "No PLC yet? Run the built-in exercise for this scene first.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
            VerticalAlignment = VerticalAlignment.Center,
        };
        exerciseLabel.AddThemeColorOverride("font_color", new Color(0.70f, 0.90f, 0.80f));
        exerciseRow.AddChild(exerciseLabel);

        var exerciseBtn = new Button { Text = " 🧪 Try this scene " };
        exerciseBtn.Pressed += () => { if (Editor is not null) TryScene.Run(Editor, IdleHint); };
        exerciseRow.AddChild(exerciseBtn);

        mainBox.AddChild(new HSeparator());

        // Driver Selection Dropdown
        mainBox.AddChild(new Label { Text = "Select Protocol Driver Target:" });

        _driverDropdown = new OptionButton
        {
            CustomMinimumSize = new Vector2(610, 36),
        };
        _driverDropdown.AddItem("🔍 Auto-Detect Active Controller (Automatic Probe)", 0);
        _driverDropdown.AddItem("Siemens PLCSIM Advanced API (Direct Shared Memory)", 1);
        _driverDropdown.AddItem("Siemens S7 Protocol (Snap7 ISO-on-TCP)", 2);
        _driverDropdown.AddItem("OPC UA Client (S7-1500 / Codesys / Beckhoff)", 3);
        _driverDropdown.AddItem("OPC UA Server (Node-RED / Ignition SCADA)", 4);
        _driverDropdown.AddItem("Modbus TCP Server (OpenPLC / SCADA)", 5);
        _driverDropdown.AddItem("Omron CX Simulator by Ismail Lowkey", 6);
        _driverDropdown.AddItem("Standalone Simulation (Internal Mock)", 7);
        _driverDropdown.ItemSelected += OnDriverSelected;
        mainBox.AddChild(_driverDropdown);

        mainBox.AddChild(new HSeparator());

        // Parameter Form
        var grid = new GridContainer { Columns = 2 };
        mainBox.AddChild(grid);

        _ipLabel = new Label { Text = "PLC IP Address / Host:" };
        grid.AddChild(_ipLabel);
        _ipInput = new LineEdit
        {
            Text = IpAddress,
            PlaceholderText = "e.g. 192.168.1.20",
            CustomMinimumSize = new Vector2(400, 32),
        };
        grid.AddChild(_ipInput);

        _portLabel = new Label { Text = "PLC Port / OPC UA URL:" };
        grid.AddChild(_portLabel);
        _portInput = new LineEdit { Text = PortOrUrl, CustomMinimumSize = new Vector2(400, 32) };
        grid.AddChild(_portInput);

        _instanceLabel = new Label { Text = "PLCSIM Instance Name:" };
        grid.AddChild(_instanceLabel);
        _instanceInput = new LineEdit
        {
            Text = InstanceName,
            PlaceholderText = "the name in the PLCSIM control panel",
            CustomMinimumSize = new Vector2(400, 32),
        };
        grid.AddChild(_instanceInput);

        _dbLabel = new Label { Text = "S7 DB Number:" };
        grid.AddChild(_dbLabel);
        _dbInput = new LineEdit { Text = DbNumber.ToString(), CustomMinimumSize = new Vector2(400, 32) };
        grid.AddChild(_dbInput);

        _modbusHostLabel = new Label { Text = "Modbus Server Bind Host:" };
        grid.AddChild(_modbusHostLabel);
        _modbusHostInput = new LineEdit
        {
            Text = ModbusHost,
            PlaceholderText = DefaultModbusHost,
            CustomMinimumSize = new Vector2(400, 32),
        };
        grid.AddChild(_modbusHostInput);

        _modbusPortLabel = new Label { Text = "Modbus Server Port:" };
        grid.AddChild(_modbusPortLabel);
        _modbusPortInput = new LineEdit
        {
            Text = ModbusPort.ToString(),
            PlaceholderText = DefaultModbusPort.ToString(),
            CustomMinimumSize = new Vector2(400, 32),
        };
        grid.AddChild(_modbusPortInput);

        _bindNote = new Label
        {
            Text = "127.0.0.1 serves this PC only. 0.0.0.0 or this PC's address lets other "
                 + "machines (OpenPLC in WSL) connect — Modbus has no password, and Windows "
                 + "may ask about the firewall.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
        };
        _bindNote.AddThemeFontSizeOverride("font_size", 11);
        _bindNote.AddThemeColorOverride("font_color", new Color(0.80f, 0.80f, 0.85f));
        mainBox.AddChild(_bindNote);

        // Panel informasi khusus Omron CX-Simulator (Direct Local Memory Injection)
        _omronInfoCard = new PanelContainer();
        var omronMargin = new MarginContainer();
        omronMargin.AddThemeConstantOverride("margin_top", 10);
        omronMargin.AddThemeConstantOverride("margin_bottom", 10);
        omronMargin.AddThemeConstantOverride("margin_left", 14);
        omronMargin.AddThemeConstantOverride("margin_right", 14);
        _omronInfoCard.AddChild(omronMargin);

        var omronBox = new VBoxContainer();
        omronMargin.AddChild(omronBox);

        var omronHeader = new Label
        {
            Text = "⚡ Direct Local Memory Injection (NetToCXSim.Core by Ismail Lowkey)",
        };
        omronHeader.AddThemeFontSizeOverride("font_size", 13);
        omronHeader.AddThemeColorOverride("font_color", new Color(0.35f, 0.90f, 1.0f));
        omronBox.AddChild(omronHeader);

        var omronDesc = new Label
        {
            Text = "Driver ini membaca dan menulis langsung ke proses memori lokal simulator Omron (CxCpuMain.exe / CxCpuMainCS2.exe) dengan zero network latency. Tidak membutuhkan konfigurasi IP Address, Port, maupun koneksi jaringan.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
        };
        omronDesc.AddThemeFontSizeOverride("font_size", 11);
        omronDesc.AddThemeColorOverride("font_color", new Color(0.85f, 0.85f, 0.90f));
        omronBox.AddChild(omronDesc);

        _omronStatusLabel = new Label
        {
            Text = "Status: Memeriksa proses CX-Simulator...",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
        };
        _omronStatusLabel.AddThemeFontSizeOverride("font_size", 11);
        omronBox.AddChild(_omronStatusLabel);

        mainBox.AddChild(_omronInfoCard);

        UpdateFormVisibility();

        mainBox.AddChild(new HSeparator());

        // Status gets its own full-width row, and it wraps.
        //
        // It used to sit inside the footer HBox with no AutowrapMode. A Label
        // without autowrap reports its whole single-line text as its minimum
        // width, and that minimum propagates up through the footer to the
        // PanelContainer. So the moment the status said anything long -- the
        // multi-sentence "no controller answered, PLCSIM cannot be probed"
        // explanation from FF-06 is ~200 characters -- the modal's minimum
        // width exceeded the viewport, CenterContainer had no slack left to
        // centre with, and Apply & Connect was pushed off the right edge of
        // the screen. The dialog broke precisely when it had something to say.
        _statusLabel = new Label
        {
            // Not "Ready": nothing is connected until the sidecar is started,
            // and saying otherwise is how this dialog used to mislead people.
            Text = "No driver running. Apply & Connect starts the Python sidecar.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
            CustomMinimumSize = new Vector2(0, 44),
            SizeFlagsHorizontal = SizeFlags.ExpandFill,
        };
        _statusLabel.AddThemeColorOverride("font_color", new Color(0.80f, 0.80f, 0.85f));
        mainBox.AddChild(_statusLabel);

        var footer = new HBoxContainer { Alignment = BoxContainer.AlignmentMode.End };
        mainBox.AddChild(footer);

        var autoBtn = new Button { Text = " 🔍 Auto-Detect Now " };
        autoBtn.Pressed += () => _ = RunAutoDetectAsync();
        footer.AddChild(autoBtn);

        var stopBtn = new Button
        {
            Text = " ⏹ Stop Sidecar ",
            TooltipText = "Stop the driver process started from this dialog",
        };
        stopBtn.Pressed += StopSidecar;
        footer.AddChild(stopBtn);

        var connectBtn = new Button { Text = " ⚡ Apply & Connect " };
        connectBtn.Pressed += ApplyConnectionSettings;
        footer.AddChild(connectBtn);

        // Show the command as well as running it. Half the value of this dialog
        // is teaching what it is doing, and the other half is still working when
        // the launch fails because python is not on PATH.
        _commandLabel = new Label
        {
            Text = "The engine speaks only the tag bus; PLC protocols live in the "
                 + "Python sidecar. Apply & Connect starts it and copies the command.",
            AutowrapMode = TextServer.AutowrapMode.WordSmart,
        };
        _commandLabel.AddThemeFontSizeOverride("font_size", 11);
        _commandLabel.AddThemeColorOverride("font_color", new Color(0.70f, 0.80f, 0.95f));
        mainBox.AddChild(_commandLabel);
    }

    public void ToggleVisibility()
    {
        Visible = !Visible;
        if (Visible)
        {
            UpdateFormVisibility();
        }
    }

    private void OnDriverSelected(long index)
    {
        if (index == 0)
        {
            _ = RunAutoDetectAsync();
            return;
        }

        // Pemilihan driver secara eksplisit menggunakan if-else terstruktur
        if (index == 1)
        {
            SelectedDriver = "plcsim-advanced";
        }
        else if (index == 2)
        {
            SelectedDriver = "s7-snap7";
        }
        else if (index == 3)
        {
            SelectedDriver = "opcua-client";
        }
        else if (index == 4)
        {
            SelectedDriver = "opcua-server";
        }
        else if (index == 5)
        {
            SelectedDriver = "modbus-tcp";
        }
        else if (index == 6)
        {
            SelectedDriver = "omron-cxsim";
        }
        else
        {
            SelectedDriver = "mock";
        }

        UpdateFormVisibility();
    }

    /// <summary>
    /// Probe for a live controller, then apply the result in a single
    /// deferred callback.
    ///
    /// Everything after the first <c>await</c> in this method used to mutate
    /// Godot UI nodes directly — <c>await</c> resumes on a thread-pool thread
    /// here, since Godot has no synchronization context to capture, so that
    /// was a real cross-thread race on scene-tree nodes (FF-07). Structuring
    /// it as "decide, then apply once via CallDeferred" fixes that and
    /// happens to make the honest-fallback fix (FF-06) a single place too.
    /// </summary>
    /// <remarks>Public so <c>--self-test=layout</c> can drive the real probe
    /// rather than a copy of its messages: the longest text this dialog can
    /// display is produced here, and that length is what broke the layout.</remarks>
    public async Task RunAutoDetectAsync()
    {
        _statusLabel.Text = "🔍 Probing network & virtual adapters for active PLC drivers...";
        _statusLabel.AddThemeColorOverride("font_color", new Color(0.9f, 0.9f, 0.3f));

        string targetIp = _ipInput.Text.Trim();
        string driver, message;
        int dropdownIndex;
        bool found;

        try
        {
            // Periksa terlebih dahulu apakah proses simulator Omron CX-Simulator sedang aktif
            bool isOmronSimRunning = false;
            try
            {
                if (System.Diagnostics.Process.GetProcessesByName("CxCpuMain").Length > 0 ||
                    System.Diagnostics.Process.GetProcessesByName("CxCpuMainCS2").Length > 0)
                {
                    isOmronSimRunning = true;
                }
            }
            catch
            {
                isOmronSimRunning = false;
            }

            if (isOmronSimRunning)
            {
                driver = "omron-cxsim";
                dropdownIndex = 6;
                found = true;
                message = "✅ Auto-Detected: Active Omron CX-Simulator process (Omron CX Simulator by Ismail Lowkey)";
            }
            else if (!string.IsNullOrEmpty(targetIp) && await ProbePortAsync(targetIp, 4840))
            {
                driver = "opcua-client";
                dropdownIndex = 3;
                found = true;
                message = $"✅ Auto-Detected: Active OPC UA Server at {targetIp}:4840";
            }
            else if (!string.IsNullOrEmpty(targetIp) && await ProbePortAsync(targetIp, 102))
            {
                driver = "s7-snap7";
                dropdownIndex = 2;
                found = true;
                message = $"✅ Auto-Detected: Active Siemens S7 CPU at {targetIp}:102";
            }
            else if (!string.IsNullOrEmpty(targetIp) && await ProbePortAsync(targetIp, 502))
            {
                driver = "modbus-tcp";
                dropdownIndex = 5;
                found = true;
                message = $"✅ Auto-Detected: Active Modbus TCP Server at {targetIp}:502";
            }
            else
            {
                // Nothing was detected — PLCSIM Advanced was never probed, it
                // is a shared-memory API with no port to probe at all. Saying
                // so honestly, instead of a green tick claiming a CPU was
                // found, is the whole fix for FF-06.
                (driver, dropdownIndex, found) = ("plcsim-advanced", 1, false);
                message = targetIp.Length == 0
                    ? "No IP entered, so only PLCSIM Advanced could be assumed. It uses a "
                    + "shared-memory API with no network port — enter an IP to probe for "
                    + "OPC UA, S7 or Modbus instead. Check the instance name below."
                    : $"No controller answered on {targetIp}. Defaulting to PLCSIM Advanced, "
                    + "which uses a shared-memory API with no network port and cannot be "
                    + "probed — check the instance name below.";
            }
        }
        catch (Exception e)
        {
            // Was previously lost entirely: a fire-and-forget task's exception
            // vanishes unless something is watching for it.
            GD.PrintErr($"auto-detect failed: {e}");
            Callable.From(() => Warn($"Auto-detect failed: {e.Message}")).CallDeferred();
            return;
        }

        Callable.From(() =>
        {
            SelectedDriver = driver;
            _driverDropdown.Select(dropdownIndex);
            _statusLabel.Text = message;
            _statusLabel.AddThemeColorOverride("font_color",
                found ? new Color(0.3f, 1.0f, 0.4f) : new Color(0.98f, 0.80f, 0.35f));
            UpdateFormVisibility();
        }).CallDeferred();
    }

    private static async Task<bool> ProbePortAsync(string host, int port)
    {
        try
        {
            using var client = new TcpClient();
            var connectTask = client.ConnectAsync(host, port);
            var timeoutTask = Task.Delay(400); // 400ms fast network probe
            var completedTask = await Task.WhenAny(connectTask, timeoutTask);
            return completedTask == connectTask && client.Connected;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Menyesuaikan tampilan form parameter secara dinamis sesuai driver yang dipilih.
    /// Driver Omron CX-Simulator (NetToCXSim.Core) mengakses memori proses lokal secara langsung,
    /// sehingga menyembunyikan input IP, Port, Instance Name, DB Number, dan Modbus setting.
    /// </summary>
    private void UpdateFormVisibility()
    {
        if (SelectedDriver == "omron-cxsim")
        {
            // Omron CX-Simulator: Direct Memory Injection (Tanpa konfigurasi jaringan)
            _ipLabel.Visible = false;
            _ipInput.Visible = false;
            _portLabel.Visible = false;
            _portInput.Visible = false;
            _instanceLabel.Visible = false;
            _instanceInput.Visible = false;
            _dbLabel.Visible = false;
            _dbInput.Visible = false;
            _modbusHostLabel.Visible = false;
            _modbusHostInput.Visible = false;
            _modbusPortLabel.Visible = false;
            _modbusPortInput.Visible = false;
            _bindNote.Visible = false;

            _omronInfoCard.Visible = true;
            UpdateOmronProcessStatus();
        }
        else if (SelectedDriver == "plcsim-advanced")
        {
            // Siemens PLCSIM Advanced: Direct Shared Memory API
            _ipLabel.Visible = false;
            _ipInput.Visible = false;
            _portLabel.Visible = false;
            _portInput.Visible = false;
            _instanceLabel.Visible = true;
            _instanceInput.Visible = true;
            _dbLabel.Visible = false;
            _dbInput.Visible = false;
            _modbusHostLabel.Visible = false;
            _modbusHostInput.Visible = false;
            _modbusPortLabel.Visible = false;
            _modbusPortInput.Visible = false;
            _bindNote.Visible = false;

            _omronInfoCard.Visible = false;
        }
        else if (SelectedDriver == "s7-snap7")
        {
            // Siemens S7 ISO-on-TCP: Butuh IP dan DB number
            _ipLabel.Visible = true;
            _ipInput.Visible = true;
            _portLabel.Visible = false;
            _portInput.Visible = false;
            _instanceLabel.Visible = false;
            _instanceInput.Visible = false;
            _dbLabel.Visible = true;
            _dbInput.Visible = true;
            _modbusHostLabel.Visible = false;
            _modbusHostInput.Visible = false;
            _modbusPortLabel.Visible = false;
            _modbusPortInput.Visible = false;
            _bindNote.Visible = false;

            _omronInfoCard.Visible = false;
        }
        else if (SelectedDriver == "opcua-client")
        {
            // OPC UA Client: Butuh IP dan Port/URL
            _ipLabel.Visible = true;
            _ipInput.Visible = true;
            _portLabel.Visible = true;
            _portInput.Visible = true;
            _instanceLabel.Visible = false;
            _instanceInput.Visible = false;
            _dbLabel.Visible = false;
            _dbInput.Visible = false;
            _modbusHostLabel.Visible = false;
            _modbusHostInput.Visible = false;
            _modbusPortLabel.Visible = false;
            _modbusPortInput.Visible = false;
            _bindNote.Visible = false;

            _omronInfoCard.Visible = false;
        }
        else if (SelectedDriver == "modbus-tcp")
        {
            // Modbus TCP Server: Butuh Host dan Port bind
            _ipLabel.Visible = false;
            _ipInput.Visible = false;
            _portLabel.Visible = false;
            _portInput.Visible = false;
            _instanceLabel.Visible = false;
            _instanceInput.Visible = false;
            _dbLabel.Visible = false;
            _dbInput.Visible = false;
            _modbusHostLabel.Visible = true;
            _modbusHostInput.Visible = true;
            _modbusPortLabel.Visible = true;
            _modbusPortInput.Visible = true;
            _bindNote.Visible = true;

            _omronInfoCard.Visible = false;
        }
        else
        {
            // Standalone / Mock / OPC UA Server
            _ipLabel.Visible = false;
            _ipInput.Visible = false;
            _portLabel.Visible = false;
            _portInput.Visible = false;
            _instanceLabel.Visible = false;
            _instanceInput.Visible = false;
            _dbLabel.Visible = false;
            _dbInput.Visible = false;
            _modbusHostLabel.Visible = false;
            _modbusHostInput.Visible = false;
            _modbusPortLabel.Visible = false;
            _modbusPortInput.Visible = false;
            _bindNote.Visible = false;

            _omronInfoCard.Visible = false;
        }
    }

    private void UpdateOmronProcessStatus()
    {
        bool isRunning = false;
        try
        {
            if (System.Diagnostics.Process.GetProcessesByName("CxCpuMain").Length > 0 ||
                System.Diagnostics.Process.GetProcessesByName("CxCpuMainCS2").Length > 0)
            {
                isRunning = true;
            }
        }
        catch
        {
            isRunning = false;
        }

        if (isRunning)
        {
            _omronStatusLabel.Text = "Status: ✅ Proses CX-Simulator terdeteksi aktif di sistem (CxCpuMain / CxCpuMainCS2). Siap terhubung!";
            _omronStatusLabel.AddThemeColorOverride("font_color", new Color(0.3f, 1.0f, 0.4f));
        }
        else
        {
            _omronStatusLabel.Text = "Status: ℹ️ CX-Simulator belum berjalan. Silakan jalankan CX-Programmer/CX-Simulator, lalu klik 'Apply & Connect'.";
            _omronStatusLabel.AddThemeColorOverride("font_color", new Color(0.95f, 0.85f, 0.4f));
        }
    }

    /// <summary>
    /// The sidecar arguments this dialog's settings amount to.
    ///
    /// <c>connect</c>, never <c>demo</c>: demo starts its own Python scene on
    /// the bus port, so against a running engine it either fails to bind or
    /// drives a scene you cannot see.
    /// </summary>
    public string BuildSidecarArguments() => BuildSidecarArguments(
        SelectedDriver, IpAddress, PortOrUrl, InstanceName, DbNumber, ModbusHost, ModbusPort,
        WillUseMappingFile() ? ProjectSettings.GlobalizePath(DriverWiringUI.MappingPath) : null);

    /// <summary>The same, from plain values: no form and no filesystem, so a
    /// self-test can check exactly what a given set of choices becomes.</summary>
    public static string BuildSidecarArguments(
        string driver, string ipAddress, string portOrUrl, string instanceName, int dbNumber,
        string modbusHost, int modbusPort, string? mappingFile)
    {
        var args = new System.Text.StringBuilder("-m factoryforge_sidecar connect");
        args.Append($" --driver {driver}");

        switch (driver)
        {
            case "opcua-client":
                string url = portOrUrl.StartsWith("opc.tcp://")
                    ? portOrUrl
                    : $"opc.tcp://{ipAddress}:4840";
                args.Append($" -o url {url}");
                break;

            case "s7-snap7":
                args.Append($" -o host {ipAddress} -o db {dbNumber}");
                break;

            case "plcsim-advanced":
                args.Append($" -o instance {instanceName}");
                break;

            case "modbus-tcp":
                // A server: the controller connects to it, so what it needs is
                // where to listen (IP-37). Written out even at the default, so
                // the copied command shows the bind instead of hiding it.
                args.Append($" -o host {modbusHost} -o port {modbusPort}");
                break;

            case "opcua-server":
                // Also a server, with no bind option in this dialog.
                break;

            case "omron-cxsim":
                // Omron CX-Simulator driver by Ismail Lowkey
                // Direct live memory access into CxCpuMain / CxCpuMainCS2
                break;
        }

        // The F4 wiring panel writes one mapping file, and every *client*
        // driver reads tag->address translations from it — only opcua-client
        // used to actually receive it. modbus-tcp and opcua-server derive
        // their own addressing (a deterministic tag-sorted table and
        // ns=2;s=<tag_id> respectively) and ignore a mapping file entirely,
        // so passing one to them would be a no-op that looks like it did
        // something. See FF-05.
        if (mappingFile is not null) args.Append($" --mapping \"{mappingFile}\"");

        return args.ToString();
    }

    /// <summary>Whether <see cref="BuildSidecarArguments"/> will actually pass
    /// the F4 wiring file for the currently selected driver — used to tell the
    /// operator plainly rather than let them assume it always does.</summary>
    private bool WillUseMappingFile()
    {
        bool isSupportedDriver = SelectedDriver == "opcua-client" ||
                                 SelectedDriver == "s7-snap7" ||
                                 SelectedDriver == "plcsim-advanced" ||
                                 SelectedDriver == "omron-cxsim";

        if (isSupportedDriver && Godot.FileAccess.FileExists(DriverWiringUI.MappingPath))
        {
            return true;
        }

        return false;
    }

    /// <summary>
    /// Check a Modbus bind the user typed and take it only if both halves are
    /// valid: a good host with a bad port changes neither, so a refused form
    /// never leaves half of itself in the next command.
    /// </summary>
    /// <returns>True if applied; otherwise <paramref name="error"/> says why.</returns>
    public bool TryApplyModbusBind(string hostText, string portText, out string error)
    {
        string host = hostText.Trim();
        string portStr = portText.Trim();

        if (!int.TryParse(portStr, out int port) || port < 1 || port > 65535)
        {
            error = $"Modbus port '{portStr}' is not a port: enter a number from 1 to 65535 "
                  + $"(default {DefaultModbusPort}).";
            return false;
        }

        // IPv4, IPv6 or a DNS name. Anything else -- a space, a URL, a stray
        // colon-and-port -- would reach the sidecar as a bind it cannot make.
        if (host.Length == 0 || Uri.CheckHostName(host) == UriHostNameType.Unknown)
        {
            error = $"Modbus host '{host}' is not an IP address or host name. "
                  + $"Use {DefaultModbusHost} for this PC only, or 0.0.0.0 / this PC's address "
                  + "for a PLC elsewhere.";
            return false;
        }

        ModbusHost = host;
        ModbusPort = port;
        error = "";
        return true;
    }

    /// <summary>Read the form, start the sidecar, and report what happened.</summary>
    public void ApplyConnectionSettings()
    {
        // Pastikan SelectedDriver sinkron secara mutlak dengan item yang dipilih pada dropdown
        int selectedIndex = _driverDropdown.Selected;
        if (selectedIndex == 1)
        {
            SelectedDriver = "plcsim-advanced";
        }
        else if (selectedIndex == 2)
        {
            SelectedDriver = "s7-snap7";
        }
        else if (selectedIndex == 3)
        {
            SelectedDriver = "opcua-client";
        }
        else if (selectedIndex == 4)
        {
            SelectedDriver = "opcua-server";
        }
        else if (selectedIndex == 5)
        {
            SelectedDriver = "modbus-tcp";
        }
        else if (selectedIndex == 6)
        {
            SelectedDriver = "omron-cxsim";
        }
        else if (selectedIndex == 7)
        {
            SelectedDriver = "mock";
        }

        // Validasi input Modbus hanya jika driver Modbus yang dipilih
        if (SelectedDriver == "modbus-tcp"
            && !TryApplyModbusBind(_modbusHostInput.Text, _modbusPortInput.Text, out string bindError))
        {
            Warn($"Not started. {bindError}");
            return;
        }

        IpAddress = _ipInput.Text.Trim();
        PortOrUrl = _portInput.Text.Trim();
        InstanceName = _instanceInput.Text.Trim();
        if (int.TryParse(_dbInput.Text, out int db)) DbNumber = db;
        SaveLastSettings();

        // Resolution order lives in SidecarLocator: an explicit
        // FACTORYFORGE_SIDECAR override, then beside the binary (a release),
        // then beside engine/ (a checkout). The old single guess was correct
        // only in a checkout, so in a packaged build F5's "command copied, run
        // it yourself" was the normal path rather than the exception (UX-04).
        SidecarLocation? found = SidecarLocator.Find();
        string arguments = BuildSidecarArguments();

        if (found is not { } sidecar)
        {
            // Name every place that was tried: "not found" without a list is a
            // dead end for anyone whose layout is unusual.
            string looked = string.Join(", ", SidecarLocator.SearchDirs());
            string fallback = $"python {arguments}";
            _commandLabel.Text = fallback;
            DisplayServer.ClipboardSet(fallback);
            Warn($"Command copied, but no sidecar was found. Looked in: {looked}. "
                 + $"Set {SidecarLocator.OverrideVar} to its directory.");
            return;
        }

        // A frozen sidecar runs itself; a checkout needs an interpreter in
        // front. What lands on the clipboard is the command that will actually
        // run, so the two never disagree.
        string command = sidecar.CommandFor(arguments);
        string display = $"cd \"{sidecar.WorkingDir}\" && {command}";
        _commandLabel.Text = display;
        DisplayServer.ClipboardSet(display);
        GD.Print($"Sidecar command (copied to clipboard):\n  {display}");

        LaunchSidecar(sidecar, command);
    }

    /// <summary>
    /// Start the sidecar as a detached process.
    ///
    /// The engine speaks the tag bus and nothing else — every PLC protocol lives
    /// in the Python sidecar — so "connect" has to mean "start that". If python
    /// is not on PATH this says so and falls back to the copied command, rather
    /// than the previous behaviour of printing a line and pretending.
    /// </summary>
    private void LaunchSidecar(SidecarLocation sidecar, string command)
    {
        if (_sidecarPid > 0 && OS.IsProcessRunning(_sidecarPid))
        {
            OS.Kill(_sidecarPid);
            _sidecarPid = 0;
        }

        int pid = TerminalLauncher.Spawn(sidecar.WorkingDir, command);
        if (pid <= 0)
        {
            Warn("Could not start python. The command is on your clipboard — run it yourself.");
            return;
        }

        _sidecarPid = pid;
        _statusLabel.AddThemeColorOverride("font_color", new Color(0.3f, 1.0f, 0.4f));

        string wiring;
        if (SelectedDriver == "omron-cxsim")
        {
            wiring = "direct memory injection (NetToCXSim.Core by Ismail Lowkey)";
        }
        else if (SelectedDriver == "modbus-tcp")
        {
            wiring = $"serving {ModbusHost}:{ModbusPort}";
        }
        else if (WillUseMappingFile())
        {
            wiring = "using F4 wiring";
        }
        else
        {
            wiring = "no wiring file — driver defaults";
        }

        _statusLabel.Text = $"Sidecar running (pid {pid}) — driver '{SelectedDriver}', {wiring}";
        Visible = false;
    }

    private void Warn(string message)
    {
        _statusLabel.AddThemeColorOverride("font_color", new Color(1.0f, 0.65f, 0.35f));
        _statusLabel.Text = message;
        GD.PrintErr(message);
    }

    private void StopSidecar()
    {
        if (_sidecarPid <= 0 || !OS.IsProcessRunning(_sidecarPid))
        {
            _statusLabel.Text = "No sidecar started from here.";
            return;
        }

        OS.Kill(_sidecarPid);
        _statusLabel.Text = $"Stopped sidecar (pid {_sidecarPid})";
        _sidecarPid = 0;
    }

    public override void _ExitTree()
    {
        // Don't leave a driver holding an OPC UA session after the window closes.
        if (_sidecarPid > 0 && OS.IsProcessRunning(_sidecarPid)) OS.Kill(_sidecarPid);
    }
}
